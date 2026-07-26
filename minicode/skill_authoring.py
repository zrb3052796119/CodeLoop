from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from minicode.capability_registry import CapabilityRegistry, get_registry
from minicode.skills import discover_skill_directories, discover_skills


_STRONG_DIRECTORY_SCORE = 1.5
_STOP_WORDS = {
    "and", "or", "the", "for", "with", "from", "this", "that", "how", "why",
    "what", "when", "where", "skills", "skill", "workflow", "workflows",
}


def propose_skill(
    cwd: str | Path,
    spec: dict[str, Any],
    registry: CapabilityRegistry | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    requested_name = _required_text(spec, "name")
    name = _slugify(requested_name)
    description = _required_text(spec, "description")
    keywords = _list_field(spec, "keywords")
    domains = _list_field(spec, "domains")
    scopes = _list_field(spec, "scopes")
    tools = _list_field(spec, "tools")
    examples = _list_field(spec, "examples")
    content_outline = str(spec.get("content_outline", "") or "").strip()
    top_k = max(1, int(spec.get("top_k", top_k) or top_k))

    registry = registry or get_registry()
    directories = discover_skill_directories(cwd)
    skills = discover_skills(cwd)
    candidates = _score_directories(
        directories,
        skills,
        name=" ".join([requested_name, name]),
        description=description,
        keywords=keywords,
        domains=domains,
        scopes=scopes,
        tools=tools,
        examples=examples,
        content_outline=content_outline,
    )[:top_k]

    strongest = candidates[0] if candidates else None
    use_existing_directory = bool(strongest and strongest["score"] >= _STRONG_DIRECTORY_SCORE)
    new_directory_suggestion = None
    if use_existing_directory:
        recommended_directory = str(strongest["name"])
    else:
        new_directory_suggestion = _new_directory_suggestion(requested_name, description, keywords, domains, scopes)
        recommended_directory = str(new_directory_suggestion["name"])

    target_path = f".mini-code/skills/{recommended_directory}/{name}/SKILL.md"
    qualified_name = f"{recommended_directory}/{name}"
    frontmatter = _build_frontmatter(
        name=name,
        description=description,
        directory=recommended_directory,
        domains=domains,
        scopes=scopes,
        keywords=keywords,
        tools=tools,
        examples=examples,
    )

    return {
        "recommended_directory": recommended_directory,
        "requested_name": requested_name,
        "skill_name": name,
        "recommendation_type": "existing_directory" if use_existing_directory else "new_directory",
        "confidence": _confidence(candidates, use_existing_directory),
        "candidate_directories": candidates,
        "target_path": target_path,
        "qualified_name": qualified_name,
        "frontmatter": frontmatter,
        "duplicate_warnings": _duplicate_warnings(
            skills,
            name=name,
            qualified_name=qualified_name,
            description=description,
        ),
        "tool_validation": _tool_validation(tools, registry),
        "new_directory_suggestion": new_directory_suggestion,
        "next_step": (
            "Review this proposal. If it is correct, create the target SKILL.md "
            "with write_file so normal path and diff approval still apply."
        ),
    }


def format_skill_proposal(proposal: dict[str, Any]) -> str:
    lines = [
        "PROPOSE_SKILL_RESULT",
        "",
        f"recommended_directory: {proposal['recommended_directory']}",
        f"requested_name: {proposal['requested_name']}",
        f"skill_name: {proposal['skill_name']}",
        f"recommendation_type: {proposal['recommendation_type']}",
        f"confidence: {proposal['confidence']}",
        f"target_path: {proposal['target_path']}",
        f"qualified_name: {proposal['qualified_name']}",
        "",
        "candidate_directories:",
    ]
    for candidate in proposal["candidate_directories"]:
        reasons = ", ".join(candidate.get("reasons", [])) or "no matching signals"
        lines.append(
            f"- {candidate['name']} score={candidate['score']}: "
            f"{candidate.get('description', '')} ({reasons})"
        )
    if not proposal["candidate_directories"]:
        lines.append("- none")

    if proposal.get("new_directory_suggestion"):
        suggestion = proposal["new_directory_suggestion"]
        lines.extend(
            [
                "",
                "new_directory_suggestion:",
                f"- name: {suggestion['name']}",
                f"- path: {suggestion['path']}",
                f"- description: {suggestion['description']}",
            ]
        )

    lines.extend(["", "duplicate_warnings:"])
    for warning in proposal["duplicate_warnings"]:
        lines.append(f"- {warning['type']}: {warning['message']}")
    if not proposal["duplicate_warnings"]:
        lines.append("- none")

    lines.extend(["", "tool_validation:"])
    for item in proposal["tool_validation"]:
        if item["known"]:
            lines.append(f"- {item['name']}: known ({item['domain']}/{item['scope']})")
        else:
            lines.append(f"- {item['name']}: unknown")
    if not proposal["tool_validation"]:
        lines.append("- none")

    lines.extend(
        [
            "",
            "frontmatter:",
            "```yaml",
            proposal["frontmatter"].strip(),
            "```",
            "",
            "json_summary:",
            "```json",
            json.dumps(proposal, ensure_ascii=False, indent=2),
            "```",
            "",
            f"next_step: {proposal['next_step']}",
        ]
    )
    return "\n".join(lines)


def _score_directories(
    directories,
    skills,
    *,
    name: str,
    description: str,
    keywords: list[str],
    domains: list[str],
    scopes: list[str],
    tools: list[str],
    examples: list[str],
    content_outline: str,
) -> list[dict[str, Any]]:
    proposed_text = " ".join([name, description, *keywords, *domains, *scopes, *tools, *examples, content_outline])
    proposed_tokens = _tokens(proposed_text)
    proposed_domains = {_normalize(domain) for domain in domains}
    proposed_scopes = {_normalize(scope) for scope in scopes}
    proposed_keywords = {_normalize(keyword) for keyword in keywords}

    skills_by_directory: dict[str, list[Any]] = {}
    for skill in skills:
        if skill.directory:
            skills_by_directory.setdefault(skill.directory, []).append(skill)

    candidates: list[dict[str, Any]] = []
    for directory in directories:
        directory_skills = skills_by_directory.get(directory.name, [])
        directory_text = " ".join(
            [
                directory.name,
                directory.description,
                *directory.domains,
                *directory.scopes,
                *directory.keywords,
                *[skill.name for skill in directory_skills],
                *[skill.description for skill in directory_skills],
                *[keyword for skill in directory_skills for keyword in skill.keywords],
            ]
        )
        directory_tokens = _tokens(directory_text)
        score = 0.0
        reasons: list[str] = []

        domain_overlap = sorted(proposed_domains & {_normalize(domain) for domain in directory.domains})
        if domain_overlap:
            score += len(domain_overlap) * 2.0
            reasons.append("domains:" + ",".join(domain_overlap))

        scope_overlap = sorted(proposed_scopes & {_normalize(scope) for scope in directory.scopes})
        if scope_overlap:
            score += len(scope_overlap) * 1.0
            reasons.append("scopes:" + ",".join(scope_overlap))

        keyword_overlap = sorted(proposed_keywords & {_normalize(keyword) for keyword in directory.keywords})
        if keyword_overlap:
            score += len(keyword_overlap) * 1.5
            reasons.append("keywords:" + ",".join(keyword_overlap))

        token_overlap = sorted(proposed_tokens & directory_tokens)
        if token_overlap:
            score += min(len(token_overlap), 8) * 0.25
            reasons.append("text:" + ",".join(token_overlap[:6]))

        similarity = _max_skill_similarity(proposed_tokens, directory_skills)
        if similarity > 0:
            score += similarity * 3.0
            reasons.append(f"similar-skill:{similarity:.2f}")

        candidates.append(
            {
                "name": directory.name,
                "description": directory.description,
                "path": directory.path,
                "source": directory.source,
                "score": round(score, 3),
                "reasons": reasons,
            }
        )

    return sorted(candidates, key=lambda item: (-item["score"], str(item["name"])))


def _duplicate_warnings(skills, *, name: str, qualified_name: str, description: str) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    normalized_name = _normalize(name)
    normalized_qualified = _normalize(qualified_name)
    description_tokens = _tokens(description)
    for skill in skills:
        skill_name = _normalize(skill.name)
        skill_qualified = _normalize(skill.qualified_name or skill.name)
        if skill_name == normalized_name:
            warnings.append(
                {
                    "type": "duplicate_name",
                    "skill": skill.qualified_name or skill.name,
                    "message": f"Skill name '{name}' already exists as {skill.qualified_name or skill.name}.",
                }
            )
        if skill_qualified == normalized_qualified:
            warnings.append(
                {
                    "type": "duplicate_qualified_name",
                    "skill": skill.qualified_name or skill.name,
                    "message": f"Qualified skill '{qualified_name}' already exists.",
                }
            )
        similarity = _jaccard(description_tokens, _tokens(skill.description))
        if similarity >= 0.65:
            warnings.append(
                {
                    "type": "similar_description",
                    "skill": skill.qualified_name or skill.name,
                    "similarity": round(similarity, 3),
                    "message": f"Description is very similar to {skill.qualified_name or skill.name}.",
                }
            )
    return warnings


def _tool_validation(tools: list[str], registry: CapabilityRegistry) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for tool_name in tools:
        capability = registry.get(tool_name)
        if capability is None:
            results.append({"name": tool_name, "known": False})
            continue
        results.append(
            {
                "name": tool_name,
                "known": True,
                "domain": capability.metadata.domain.value,
                "scope": capability.metadata.scope.value,
                "description": capability.metadata.description,
            }
        )
    return results


def _new_directory_suggestion(
    name: str,
    description: str,
    keywords: list[str],
    domains: list[str],
    scopes: list[str],
) -> dict[str, Any]:
    if domains:
        directory_name = f"{_slugify(domains[0])}-skills"
    elif keywords:
        directory_name = f"{_slugify(keywords[0])}-skills"
    else:
        directory_name = f"{_slugify(name)}-skills"
    return {
        "name": directory_name,
        "path": f".mini-code/skills/{directory_name}/SKILL_DIR.md",
        "description": f"Skills for {description[:80].rstrip('.')}.",
        "domains": domains,
        "scopes": scopes,
        "keywords": keywords,
    }


def _confidence(candidates: list[dict[str, Any]], use_existing_directory: bool) -> str:
    if not use_existing_directory or not candidates:
        return "low"
    top = float(candidates[0]["score"])
    second = float(candidates[1]["score"]) if len(candidates) > 1 else 0.0
    if top >= 5.0 and top - second >= 1.0:
        return "high"
    return "medium"


def _max_skill_similarity(proposed_tokens: set[str], skills) -> float:
    if not proposed_tokens:
        return 0.0
    return max(
        (_jaccard(proposed_tokens, _tokens(" ".join([skill.name, skill.description, *skill.keywords]))) for skill in skills),
        default=0.0,
    )


def _build_frontmatter(
    *,
    name: str,
    description: str,
    directory: str,
    domains: list[str],
    scopes: list[str],
    keywords: list[str],
    tools: list[str],
    examples: list[str],
) -> str:
    lines = [
        "---",
        f"name: {json.dumps(name, ensure_ascii=False)}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        f"directory: {json.dumps(directory, ensure_ascii=False)}",
    ]
    if domains:
        lines.append(f"domains: {_format_inline_list(domains)}")
    if scopes:
        lines.append(f"scopes: {_format_inline_list(scopes)}")
    if tools:
        lines.append(f"tools: {_format_inline_list(tools)}")
    if keywords:
        lines.append(f"keywords: {_format_inline_list(keywords)}")
    if examples:
        lines.append("examples:")
        lines.extend(f"  - {json.dumps(example, ensure_ascii=False)}" for example in examples)
    lines.extend(["---", ""])
    return "\n".join(lines)


def _format_inline_list(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(value, ensure_ascii=False) for value in values) + "]"


def _required_text(spec: dict[str, Any], key: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _list_field(mapping: dict[str, Any], key: str) -> list[str]:
    value = mapping.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _tokens(value: str) -> set[str]:
    normalized = _normalize(value)
    return {
        part for part in re.findall(r"[a-z0-9]+", normalized)
        if len(part) > 1 and part not in _STOP_WORDS
    }


def _normalize(value: str) -> str:
    return str(value).lower().replace("_", " ").replace("-", " ").strip()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "skill"


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
