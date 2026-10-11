#!/usr/bin/env python3
"""Offline semantic-body and source-ledger verification, not product acceptance.

No network, installs, account access or source writes. Outputs are exclusive
external 3FS evidence. --inspect-body reads HTML while excluding navigation,
scripts and footer; SOLIDWORKS Help uses its decoded helpText article instead.
--selftest checks rejection controls; default verifies the completion ledger
and preserves the baseline source/failure entries byte-semantically.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = "941005d7c45a35f8cbbe8d2dc65c68093a790a3b"
LEDGER = "docs/research/official-sources.json"
OWNED = (LEDGER, "docs/research/competitive-matrix.md",
         "docs/research/industrial-workflows.md", "tools/official_research_verify.py")
OFFICIAL = ("3ds.com", "solidworks.com", "siemens.com", "sw.siemens.com",
            "onshape.com", "autodesk.com", "freecad.org", "opencascade.org")
EVIDENCE = Path("/hf3fs-jd/data/tmp")


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()


def normalized(text):
    return re.sub(r"\s+", " ", unescape(text)).strip()


class BodyParser(HTMLParser):
    """Only collect semantic article/main blocks, not matching navigation text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.blocks, self.links = [], [], []
        self.title, self.next_data = "", ""
        self.next_script = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "__NEXT_DATA__":
            self.next_script = True
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, attrs))
        if self.allowed() and tag == "a" and attrs.get("href"):
            self.links.append({"href": attrs["href"], "text": ""})

    def handle_endtag(self, tag):
        if tag == "script":
            self.next_script = False
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def allowed(self):
        if any(tag in {"script", "style", "nav", "header", "footer", "noscript"}
               for tag, attrs in self.stack):
            return False
        return any(tag in {"main", "article"} or attrs.get("role") == "main"
                   for tag, attrs in self.stack)

    def handle_data(self, data):
        if self.next_script:
            self.next_data += data
        if any(tag == "title" for tag, attrs in self.stack):
            self.title += data
        if not self.allowed() or not data.strip():
            return
        tags = [tag for tag, attrs in self.stack]
        kind = next((tag for tag in reversed(tags)
                     if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "a"}),
                    "text")
        self.blocks.append({"kind": kind, "text": normalized(data)})
        if kind == "a" and self.links:
            self.links[-1]["text"] += normalized(data)


def article(raw):
    outer = BodyParser()
    outer.feed(raw.decode("utf-8"))
    if outer.next_data:
        data = json.loads(outer.next_data)
        page = data.get("props", {}).get("pageProps", {})
        help_data = page.get("helpContentData")
        if help_data is not None:
            if help_data.get("errorDetails"):
                raise ValueError("official Help error body: " + str(help_data["errorDetails"]))
            text = help_data.get("helpText")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("official Help article missing")
            inner = BodyParser()
            inner.feed(text)
            if not inner.blocks:
                raise ValueError("decoded official Help has no semantic article")
            return {"title": normalized(inner.title) or normalized(outer.title),
                    "method": "__NEXT_DATA__.props.pageProps.helpContentData.helpText",
                    "blocks": inner.blocks, "links": inner.links}
    if not outer.blocks:
        raise ValueError("no semantic main/article body; navigation is not evidence")
    return {"title": normalized(outer.title), "method": "main/article excluding navigation",
            "blocks": outer.blocks, "links": outer.links}


def fingerprints():
    process = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                              "--exclude-standard"], cwd=ROOT, capture_output=True, check=True)
    paths = set(process.stdout.decode().split("\0")) - {""}
    return {path: sha((ROOT / path).read_bytes()) for path in sorted(paths)
            if (ROOT / path).is_file()}


def changed_paths():
    tracked = subprocess.run(["git", "diff", "--name-only", "-z", BASE], cwd=ROOT,
                             capture_output=True, check=True)
    other = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                           cwd=ROOT, capture_output=True, check=True)
    return sorted(set((tracked.stdout + other.stdout).decode().split("\0")) - {""})


def git_binding():
    commands = (("head", ["rev-parse", "HEAD"]),
                ("branch", ["branch", "--show-current"]),
                ("commits", ["rev-list", "--reverse", BASE + "..HEAD"]))
    values = {}
    for name, argv in commands:
        process = subprocess.run(["git", *argv], cwd=ROOT, capture_output=True,
                                 text=True, check=True)
        values[name] = process.stdout.strip()
    values["commits"] = values["commits"].splitlines()
    values["range"] = BASE + ".." + values["head"]
    return values


def baseline():
    process = subprocess.run(["git", "show", BASE + ":" + LEDGER], cwd=ROOT,
                             capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError("baseline read failed rc=%d: %s" % (process.returncode, process.stderr))
    return json.loads(process.stdout)


def external_path(value):
    path = Path(value)
    if not path.is_absolute() or not path.resolve().is_relative_to(EVIDENCE.resolve()):
        raise ValueError("not-external-3fs:" + str(path))
    if path.is_symlink():
        raise ValueError("linked-evidence:" + str(path))
    return path


def resolve_source(source, ledger):
    if "historical_source_id" not in source:
        return source
    candidates = [row for row in ledger["sources"]
                  if row["id"] == source["historical_source_id"]]
    if len(candidates) != 1 or candidates[0]["capture_status"] != "CAPTURED_BODY":
        raise ValueError("historical-source-not-captured")
    parent = candidates[0]
    return {**parent, **source,
            "canonical_url": parent.get("canonical_url", parent["url"].split("?")[0]),
            "capture_method": "historical_body_readback",
            "captured_at": parent["capture_started_at"]}


def verify_source(source):
    errors = []
    url = urlsplit(source["canonical_url"])
    if url.scheme != "https" or not url.hostname or not any(
            url.hostname == domain or url.hostname.endswith("." + domain) for domain in OFFICIAL):
        errors.append("nonofficial-url")
    if url.username or url.password:
        errors.append("credential-url")
    captured = datetime.fromisoformat(source["captured_at"])
    if captured.tzinfo is None or captured > datetime.now(timezone.utc):
        errors.append("invalid-capture-time")
    path = external_path(source["body_path"])
    raw = path.read_bytes()
    if source["capture_method"] not in {"historical_body_readback", "existing_astrill_url_fetch"}:
        errors.append("unsupported-capture-method")
    if not raw or len(raw) != source["bytes"] or sha(raw) != source["sha256"]:
        errors.append("body-binding")
    content = article(raw)
    text = normalized(" ".join(block["text"] for block in content["blocks"]))
    if source["subject_anchor"] not in text:
        errors.append("wrong-subject")
    if source["semantic_method"] != content["method"]:
        errors.append("semantic-method")
    if not source["facts"]:
        errors.append("empty-facts")
    for fact in source["facts"]:
        if (not fact["id"] or not fact["statement"] or not fact["anchor"]
                or normalized(fact["anchor"]) not in text):
            errors.append("fact-anchor:" + fact.get("id", "?"))
        if fact["kind"] not in {"capability", "runtime", "entitlement", "workflow", "boundary"}:
            errors.append("fact-kind")
        if not fact.get("scope") or fact.get("evidence_level") not in {
                "OFFICIAL_DESCRIPTION", "OFFICIAL_PROCEDURE"}:
            errors.append("fact-scope:" + fact["id"])
        matches = [i for i, block in enumerate(content["blocks"])
                   if normalized(fact["anchor"]) in block["text"]]
        if not matches:
            errors.append("fact-not-in-single-body-block:" + fact["id"])
        fact["validated_block_indices"] = matches
    log = external_path(source["fetch_log"]).read_text()
    terminals = [line for line in log.splitlines()
                 if line.startswith("ASTRiLL_FETCH ok=1 mode=url ")]
    if len(terminals) != 1 or source["sha256"] not in terminals[0]:
        errors.append("fetch-log-binding")
    else:
        terminal = terminals[0]
        requested = re.search(r" url=(\S+)", terminal)
        if (not requested or urlsplit(requested[1])._replace(query="", fragment="") !=
                url._replace(query="", fragment="")):
            errors.append("fetch-url-binding")
        if " bytes=%d " % source["bytes"] not in terminal:
            errors.append("fetch-bytes-binding")
        match = re.search(r"url-fetch-(\d{8})-(\d{6})-", source["fetch_log"])
        if not match or captured.strftime("%Y%m%d-%H%M%S") != "-".join(match.groups()):
            errors.append("fetch-time-binding")
    if source["actual_rc"] != 0 or source["wget_rc"] != 0:
        errors.append("fetch-rc")
    if source["captured_at"] != source["capture_started_at"]:
        errors.append("capture-time-rewritten")
    content.update(bytes=len(raw), sha256=sha(raw))
    return errors, content


def verify_ledger(ledger, original, documents=None):
    errors, rows = [], []
    for key, value in original.items():
        if ledger.get(key) != value:
            errors.append("historical-ledger-changed:" + key)
    completion = ledger["research_completion"]
    if completion["base_commit"] != BASE or completion["industrial_parity"] != "NOT_ASSESSED":
        errors.append("scope")
    if not completion["web_attempts"]:
        errors.append("missing-web-attempts")
    for attempt in completion["web_attempts"]:
        if attempt["status"] == "NOT_CAPTURED" and attempt["accepted_facts"]:
            errors.append("unsupported-web-facts")
        if attempt["status"] == "CAPTURED_PRIMARY_BODY" and not attempt["source_ids"]:
            errors.append("missing-web-source-ids")
    if completion["status"] not in {"PARTIAL", "COMPLETE"}:
        errors.append("nonterminal-research-status")
    if bool(completion["remaining"]) != (completion["status"] == "PARTIAL"):
        errors.append("remaining-status-mismatch")
    sources = completion["sources"]
    if not sources:
        errors.append("empty-completion")
    ids, fact_ids = set(), set()
    for entry in sources:
        source = deepcopy(entry)
        if source["id"] in ids:
            errors.append("duplicate-source")
        ids.add(source["id"])
        try:
            source = resolve_source(source, ledger)
            problems, content = verify_source(source)
            errors.extend(source["id"] + ":" + problem for problem in problems)
            rows.append({"id": source["id"], "errors": problems,
                         "facts": len(source["facts"]), "bytes": source["bytes"],
                         "sha256": content["sha256"], "semantic_method": content["method"],
                         "canonical_url": source["canonical_url"],
                         "captured_at": source["captured_at"],
                         "facts_readback": source["facts"]})
        except Exception as exc:
            errors.append(source["id"] + ":" + str(exc))
            rows.append({"id": source["id"], "errors": [str(exc)], "facts": 0})
        for fact in source["facts"]:
            if fact["id"] in fact_ids:
                errors.append("duplicate-fact")
            fact_ids.add(fact["id"])
    for workflow in completion["workflows"]:
        if (not workflow["id"] or not workflow["inputs"] or not workflow["steps"]
                or not workflow["outputs"] or not workflow["positive_controls"]
                or not workflow["negative_controls"] or not workflow["purchase_boundary"]
                or workflow["execution_status"] != "NOT_EXECUTED"):
            errors.append("workflow-incomplete:" + workflow["id"])
        if not workflow["fact_ids"] or set(workflow["fact_ids"]) - fact_ids:
            errors.append("workflow-source:" + workflow["id"])
        if workflow.get("basis") not in {"OFFICIAL_PROCEDURE", "RESEARCHER_PROTOCOL"}:
            errors.append("workflow-basis:" + workflow["id"])
    if not completion["workflows"] or not completion["offerings"]:
        errors.append("empty-workflow-or-offering")
    for key in ("workflows", "offerings"):
        if len({row["id"] for row in completion[key]}) != len(completion[key]):
            errors.append("duplicate-" + key)
    required_domains = {"solid", "surface", "assembly", "drawing-pmi", "sheet-metal",
                        "structure", "tooling-cam", "cae", "electrical-fluid", "pdm-plm"}
    if required_domains - {w["domain"] for w in completion["workflows"]}:
        errors.append("missing-industrial-domain")
    for offering in completion["offerings"]:
        if (not offering["name"] or not offering["purchase_boundary"]
                or set(offering["fact_ids"]) - fact_ids):
            errors.append("offering-source:" + offering["id"])
        if not offering["fact_ids"] and offering["runtime"] != "NOT_VERIFIED":
            errors.append("unsupported-offering:" + offering["id"])
        if offering["server_frame_topology"] != "NOT_VERIFIED":
            errors.append("unsupported-frame-topology")
    for path in ("docs/research/competitive-matrix.md", "docs/research/industrial-workflows.md"):
        if documents is not None:
            text = documents.get(path)
        else:
            text = (ROOT / path).read_text() if (ROOT / path).is_file() else None
        if not text:
            errors.append("missing-document:" + path)
            continue
        cited = set(re.findall(r"\[fact:([A-Za-z0-9_-]+)\]", text))
        if cited - fact_ids:
            errors.append("document-unbound-facts:" + path)
    for rejected in completion["rejected_attempts"]:
        log = external_path(rejected["fetch_log"]).read_text()
        if rejected["actual_rc"] == 2:
            if "budget_busy" not in log and "transfer_error" not in log:
                errors.append("unbound-lock-refusal")
        else:
            raw = external_path(rejected["body_path"]).read_bytes()
            if len(raw) != rejected["bytes"] or sha(raw) != rejected["sha256"]:
                errors.append("rejected-body-binding:" + rejected["id"])
            try:
                article(raw)
            except ValueError as exc:
                if rejected["semantic_error"] not in str(exc):
                    errors.append("rejected-error-mismatch:" + rejected["id"])
            else:
                errors.append("rejected-body-now-semantic:" + rejected["id"])
    return errors, rows


def selftest(out):
    cases = []
    good = b"<html><title>Subject</title><nav>wrong</nav><main><h1>Subject</h1><p>fact</p></main></html>"
    parsed = article(good)
    cases.append({"name": "semantic-positive", "pass": parsed["blocks"][1]["text"] == "fact"})
    cases.append({"name": "navigation-excluded", "pass": all(
        block["text"] != "wrong" for block in parsed["blocks"])})
    for name, raw in (
            ("navigation-only-rejected", b"<nav>Subject fact</nav>"),
            ("script-only-rejected", b"<script>Subject fact</script>"),
            ("empty-rejected", b""),
            ("help-error-rejected", b'<script id="__NEXT_DATA__">{"props":{"pageProps":'
             b'{"helpContentData":{"errorDetails":"File does not exist","helpText":""}}}}</script>')):
        try:
            article(raw)
        except ValueError:
            accepted = True
        else:
            accepted = False
        cases.append({"name": name, "pass": accepted})
    help_good = b'<script id="__NEXT_DATA__">{"props":{"pageProps":{"helpContentData":' \
        b'{"errorDetails":null,"helpText":"<main><h1>Real topic</h1><p>fact</p></main>"}}}}</script>'
    cases.append({"name": "help-decoded-positive", "pass":
                  article(help_good)["blocks"][0]["text"] == "Real topic"})
    cases.append({"name": "void-tags-do-not-leak-nav", "pass": article(
        b"<main><img><p>fact</p></main><nav>noise</nav>")["blocks"] ==
        [{"kind": "p", "text": "fact"}]})
    ledger = json.loads((ROOT / LEDGER).read_text())
    original = baseline()
    documents = {path: (ROOT / path).read_text() for path in OWNED if path.endswith(".md")}
    errors, rows = verify_ledger(ledger, original, documents)
    cases.append({"name": "real-ledger-positive", "pass": not errors, "errors": errors})
    mutations = [
        ("historical-failure-preserved", lambda x: x.update(owner="wrong"), "historical-ledger-changed"),
        ("empty-sources-rejected", lambda x: x["research_completion"].update(sources=[]), "empty-completion"),
        ("hash-corruption-rejected", lambda x: x["research_completion"]["sources"][-1].update(
            sha256="0" * 64), "body-binding"),
        ("official-but-wrong-url-rejected", lambda x: x["research_completion"]["sources"][-1].update(
            canonical_url="https://www.siemens.com/another-topic/"), "fetch-url-binding"),
        ("capture-time-corruption-rejected", lambda x: x["research_completion"]["sources"][-1].update(
            captured_at="2026-10-12T01:52:59+08:00"), "fetch-time-binding"),
        ("byte-count-corruption-rejected", lambda x: x["research_completion"]["sources"][-1].update(
            bytes=1), "fetch-bytes-binding"),
        ("wrong-subject-rejected", lambda x: x["research_completion"]["sources"][-1].update(
            subject_anchor="not a topic in this body"), "wrong-subject"),
        ("navigation-fact-rejected", lambda x: x["research_completion"]["sources"][-1]["facts"][0].update(
            anchor="not an article fact"), "fact-anchor"),
        ("unknown-fact-rejected", lambda x: x["research_completion"]["workflows"][0].update(
            fact_ids=["unknown-fact"]), "workflow-source"),
        ("missing-negative-control-rejected", lambda x: x["research_completion"]["workflows"][0].update(
            negative_controls=[]), "workflow-incomplete"),
        ("invented-execution-rejected", lambda x: x["research_completion"]["workflows"][0].update(
            execution_status="PASS"), "workflow-incomplete"),
        ("server-topology-rejected", lambda x: x["research_completion"]["offerings"][0].update(
            server_frame_topology="SERVER_GPU"), "unsupported-frame-topology"),
        ("unavailable-web-not-evidence", lambda x: x["research_completion"]["web_attempts"][0].update(
            accepted_facts=["invented"]), "unsupported-web-facts"),
        ("missing-domain-rejected", lambda x: x["research_completion"].update(workflows=[
            w for w in x["research_completion"]["workflows"] if w["domain"] != "cae"]),
         "missing-industrial-domain"),
    ]
    for name, mutate, expected in mutations:
        variant = deepcopy(ledger)
        mutate(variant)
        found, unused = verify_ledger(variant, original, documents)
        cases.append({"name": name, "pass": any(expected in e for e in found),
                      "expected_error": expected, "errors": found})
    bad_documents = dict(documents)
    bad_documents.pop("docs/research/industrial-workflows.md")
    found, unused = verify_ledger(ledger, original, bad_documents)
    cases.append({"name": "missing-doc-counts-survive", "pass":
                  "missing-document:docs/research/industrial-workflows.md" in found
                  and len(unused) == len(rows)})
    bad_documents = dict(documents)
    bad_documents["docs/research/industrial-workflows.md"] += "\n[fact:unknown-fact]\n"
    found, unused = verify_ledger(ledger, original, bad_documents)
    cases.append({"name": "document-unknown-fact-rejected", "pass":
                  any("document-unbound-facts" in e for e in found)})
    write(out / "result.json", {"verdict": "PASS" if all(c["pass"] for c in cases) else "FAIL",
                               "checks": cases, "count": len(cases),
                               "passed": sum(c["pass"] for c in cases), "finished_at": now(),
                               "scope": "offline semantic validator; no product test"})
    with (out / "selftest.log").open("x") as stream:
        stream.write("OFFICIAL_SELFTEST count=%d passed=%d rc=%d at=%s\n" % (
            len(cases), sum(c["pass"] for c in cases),
            0 if all(c["pass"] for c in cases) else 1, now()))
    return 0 if all(c["pass"] for c in cases) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--inspect-body", type=Path)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--contains", default="")
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()):
        parser.error("output must be new external 3FS evidence")
    out.mkdir(parents=True, exist_ok=False)
    if args.selftest:
        rc = selftest(out)
        print("OFFICIAL_SELFTEST rc=%d result=%s" % (rc, out / "result.json"))
        return rc
    if args.inspect_body:
        raw = args.inspect_body.read_bytes()
        try:
            content = article(raw)
        except Exception as exc:
            write(out / "result.json", {"verdict": "FAIL", "actual_rc": 1,
                  "body_path": str(args.inspect_body), "bytes": len(raw), "sha256": sha(raw),
                  "first_cause": str(exc), "finished_at": now()})
            print("OFFICIAL_ARTICLE verdict=FAIL cause=%s result=%s" % (exc, out / "result.json"))
            return 1
        write(out / "article.json", content)
        print("OFFICIAL_ARTICLE bytes=%d sha256=%s title=%s method=%s" % (
            len(raw), sha(raw), content["title"], content["method"]))
        selected = [block for block in content["blocks"]
                    if (args.contains.casefold() in block["text"].casefold()
                        if args.contains else block["kind"].startswith("h"))]
        for block in selected[:70]:
            print("%s: %s" % (block["kind"], block["text"][:700]))
        for link in content["links"][:100]:
            print("LINK %s %s" % (link["text"][:100], link["href"]))
        return 0
    before = fingerprints()
    result = {"verdict": "FAIL", "started_at": now(), "base_commit": BASE,
              "source_before": before, "source_root": str(ROOT),
              "tool_sha256": sha(Path(__file__).read_bytes()), "git_binding": git_binding()}
    try:
        ledger = json.loads((ROOT / LEDGER).read_text())
        errors, rows = verify_ledger(ledger, baseline())
        changed = changed_paths()
        errors.extend("outside-owned-write:" + path for path in changed if path not in OWNED)
        result.update(errors=errors, source_rows=rows, source_count=len(rows),
                      facts=sum(row["facts"] for row in rows),
                      workflows=len(ledger["research_completion"]["workflows"]),
                      offerings=len(ledger["research_completion"]["offerings"]),
                      changed_paths=changed, verdict="FAIL" if errors else "PASS",
                      source_file_count=len(before),
                      historical_fields_preserved=len(baseline()),
                      historical_sources=len(ledger["sources"]),
                      new_primary_bodies=sum("historical_source_id" not in s
                          for s in ledger["research_completion"]["sources"]),
                      historical_bodies_reused=sum("historical_source_id" in s
                          for s in ledger["research_completion"]["sources"]),
                      new_rejected_attempts=len(ledger["research_completion"]["rejected_attempts"]),
                      research_status=ledger["research_completion"]["status"],
                      remaining=ledger["research_completion"]["remaining"])
    except Exception:
        result["first_cause"] = traceback.format_exc()
    after = fingerprints()
    result.update(source_after=after, source_drift=sorted(
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path)),
        source_fingerprint=sha(json.dumps(before, sort_keys=True).encode()),
        finished_at=now(), industrial_parity="NOT_ASSESSED",
        product_workflows_executed=0)
    if result["source_drift"]:
        result["verdict"] = "FAIL"
    result["actual_rc"] = 0 if result["verdict"] == "PASS" else 1
    write(out / "result.json", result)
    line = "OFFICIAL_RESEARCH verdict=%s sources=%s facts=%s workflows=%s drift=%d at=%s result=%s" % (
        result["verdict"], result.get("source_count", -1), result.get("facts", -1),
        result.get("workflows", -1), len(result["source_drift"]), result["finished_at"], out / "result.json")
    with (out / "verification.log").open("x") as stream:
        stream.write(line + "\n")
        if result.get("first_cause"):
            stream.write(result["first_cause"])
        for error in result.get("errors", []):
            stream.write(error + "\n")
    print(line)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
