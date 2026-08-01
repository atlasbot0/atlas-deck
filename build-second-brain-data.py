#!/usr/bin/env python3
"""Extract the Atlas Second Brain graph data from Obsidian vaults + canonical outputs.

Emits second-brain-data.json for the D3 visualisation at second-brain.html.

Nightly refresh planned; for now this is invoked manually before deploy.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

VAULTS = {
    "rollie": Path("/Users/atlas/ObsidianVaults/Rollie"),
    "personal": Path("/Users/atlas/ObsidianVaults/Personal and Professional Development"),
}
OUTPUTS_ROOT = Path("/Users/atlas/Code/vince-agents/outputs")

OUT = Path("/Users/atlas/.openclaw/workspace/atlas-deck/second-brain-data.json")


def parse_frontmatter(md: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---", md, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith("  ") and not line.startswith("-"):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'").strip("[").strip("]")
    return fm


def scan_projects(vault_key: str, vault_path: Path) -> list[dict]:
    """Return one dict per project README under Projects/{Active,Completed,Archived}/."""
    projects = []
    projects_root = vault_path / "Projects"
    if not projects_root.exists():
        return projects
    for status_dir in ["Active", "Completed", "Archived"]:
        sd = projects_root / status_dir
        if not sd.exists():
            continue
        # Find all README.md under this status (could be nested by subgroup)
        for readme in sd.rglob("README.md"):
            try:
                content = readme.read_text(errors="replace")
                fm = parse_frontmatter(content)
                slug = fm.get("slug") or readme.parent.name
                # Detect subgroup (folder between status and slug)
                rel = readme.parent.relative_to(sd)
                parts = rel.parts
                subgroup = parts[0] if len(parts) > 1 else None
                projects.append({
                    "id": f"{vault_key}:{slug}",
                    "type": "project",
                    "slug": slug,
                    "label": slug,
                    "vault": vault_key,
                    "status": status_dir,
                    "subgroup": subgroup,
                    "summary": (fm.get("summary") or "").strip()[:280]
                        or _extract_current_state(content)[:280]
                        or "(no summary yet)",
                    "last_touched": fm.get("last_touched", ""),
                    "canonical_path": fm.get("canonical_outputs", "").strip(),
                    "readme_path": str(readme),
                    "tags": _clean_tags(fm.get("tags", "")),
                })
            except Exception as e:
                continue
    return projects


def _extract_current_state(md: str) -> str:
    """Pull the paragraph under '## Current state' as fallback summary."""
    m = re.search(r"## Current state\s*\n\n(.+?)(?=\n\n|\n##)", md, re.DOTALL)
    if m:
        return m.group(1).strip().replace("\n", " ")
    return ""


def _clean_tags(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r",\s*", raw.strip("[]").strip())
    return [p.strip().strip("'\"") for p in parts if p.strip()]


def scan_doctrine(vault_key: str, vault_path: Path) -> list[dict]:
    """Find doctrine files: Projects/How to work on projects.md, 5 Wiki/Concepts/*doctrine*, etc."""
    doctrines = []
    candidates = [
        vault_path / "Projects" / "How to work on projects.md",
    ]
    concepts = vault_path / "5 Wiki" / "Concepts"
    if concepts.exists():
        for f in concepts.glob("*.md"):
            name = f.stem.lower()
            if "doctrine" in name or "voice" in name or name.endswith(("checklist", "framework")):
                candidates.append(f)
    for c in candidates:
        if c.exists():
            try:
                content = c.read_text(errors="replace")
                fm = parse_frontmatter(content)
                doctrines.append({
                    "id": f"doctrine:{vault_key}:{c.stem}",
                    "type": "doctrine",
                    "label": c.stem,
                    "vault": vault_key,
                    "summary": (fm.get("summary", "") or fm.get("description", "") or "").strip()[:280] or "Vault doctrine — rules that fire in every session",
                    "path": str(c),
                })
            except Exception:
                continue
    return doctrines


def scan_canonical_outputs() -> dict[str, list[dict]]:
    """Return {project_slug: [{summary, path, date, ...}, ...]} for the most recent 3 outputs per project."""
    by_project = {}
    for pdir in OUTPUTS_ROOT.iterdir():
        if not pdir.is_dir():
            continue
        slug = pdir.name
        mds = sorted(pdir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        for md in mds:
            try:
                content = md.read_text(errors="replace")
                fm = parse_frontmatter(content)
                if not (fm.get("date") and fm.get("summary")):
                    continue
                by_project.setdefault(slug, []).append({
                    "id": f"canon:{slug}:{md.stem}",
                    "type": "canonical",
                    "label": md.stem[:60],
                    "project": slug,
                    "summary": fm["summary"][:220],
                    "date": fm.get("date", ""),
                    "status": fm.get("decision_status", ""),
                    "path": str(md),
                })
            except Exception:
                continue
    return by_project


def build_graph() -> dict:
    nodes = []
    links = []

    # Root
    nodes.append({"id": "root", "type": "root", "label": "Second Brain", "summary": "Obsidian is the source of truth. Everything else is disposable. Two vaults, ~40 active projects, doctrine at each root, canonical outputs linked from every README."})

    # Vaults
    for vault_key, vault_path in VAULTS.items():
        vault_id = f"vault:{vault_key}"
        vault_label = "Rollie" if vault_key == "rollie" else "Personal"
        nodes.append({
            "id": vault_id,
            "type": "vault",
            "label": vault_label,
            "vault": vault_key,
            "summary": f"The {vault_label} vault — {'business projects, Rollie recon, Klaviyo, brand work.' if vault_key == 'rollie' else 'personal, atlas-os, Vince Studio creative, AI workflows, coaching, life admin.'}",
        })
        links.append({"source": "root", "target": vault_id, "kind": "vault"})

    # Doctrine files
    for vault_key, vault_path in VAULTS.items():
        for d in scan_doctrine(vault_key, vault_path):
            nodes.append(d)
            links.append({"source": f"vault:{vault_key}", "target": d["id"], "kind": "doctrine"})

    # Projects
    all_projects = []
    for vault_key, vault_path in VAULTS.items():
        pjs = scan_projects(vault_key, vault_path)
        all_projects.extend(pjs)
        for p in pjs:
            nodes.append(p)
            links.append({"source": f"vault:{vault_key}", "target": p["id"], "kind": p["status"].lower()})

    # Canonical outputs — link from project → output
    canon = scan_canonical_outputs()
    for project in all_projects:
        # Match by slug
        outputs = canon.get(project["slug"], [])
        for o in outputs:
            oid = o["id"] + ":" + project["id"]  # unique per project-output link
            oo = dict(o)
            oo["id"] = oid
            nodes.append(oo)
            links.append({"source": project["id"], "target": oid, "kind": "output"})

    # Cross-project wikilinks — scan project READMEs for [[project-name]] or [[slug]] references
    slug_to_id = {p["slug"]: p["id"] for p in all_projects}
    for p in all_projects:
        try:
            content = Path(p["readme_path"]).read_text(errors="replace")
            # find wikilinks
            for m in re.finditer(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", content):
                ref = m.group(1).strip().lower()
                # match on slug substring
                for slug, other_id in slug_to_id.items():
                    if slug != p["slug"] and (slug in ref or ref in slug):
                        links.append({"source": p["id"], "target": other_id, "kind": "wikilink"})
                        break
        except Exception:
            continue

    stats = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "nodes": len(nodes),
        "links": len(links),
        "projects_active": sum(1 for p in all_projects if p["status"] == "Active"),
        "projects_completed": sum(1 for p in all_projects if p["status"] == "Completed"),
        "projects_archived": sum(1 for p in all_projects if p["status"] == "Archived"),
        "doctrine_files": sum(1 for n in nodes if n.get("type") == "doctrine"),
        "canonical_outputs": sum(1 for n in nodes if n.get("type") == "canonical"),
    }

    return {"stats": stats, "nodes": nodes, "links": links}


def main():
    data = build_graph()
    OUT.write_text(json.dumps(data, indent=1))
    print(f"wrote {OUT}")
    for k, v in data["stats"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
