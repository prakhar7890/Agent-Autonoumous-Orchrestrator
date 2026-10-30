"""Held-out routing evaluation (memory backend).

20 paraphrased requests that are NOT copied from any seed agent's `example_queries`, each with the
agent a human would expect. The test measures routing accuracy and checks that the number recorded
in `docs/ROUTING_EVAL.md` still matches the live run (so the doc cannot silently drift). It does
**not** assert an accuracy threshold: the point is to report generalisation honestly.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_repo.graph.store import InMemoryGraph
from agent_repo.repository import AgentRepository
from agent_repo.routing import Router
from agent_repo.seeds import seed_repository
from agent_repo.tools import builtin_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DOC = REPO_ROOT / "docs" / "ROUTING_EVAL.md"
MAINTAINER = "alice"

# (query, attachment filename or None, expected goal agent)
HELD_OUT: tuple[tuple[str, str | None, str], ...] = (
    ("boil down this report to its main ideas", None, "text_summarizer"),
    ("distill the essential points from these paragraphs", None, "text_summarizer"),
    ("shorten this long write-up but keep the gist", None, "text_summarizer"),
    ("what are the highlights of this text", None, "text_summarizer"),
    ("produce a visual of a mountain lake at dawn", None, "image_generator"),
    ("render an illustration of a robot holding a flower", None, "image_generator"),
    ("I need artwork depicting a futuristic city", None, "image_generator"),
    ("make a drawing of a house for my slide", None, "image_generator"),
    ("shoot this message over to my manager by email", None, "email_sender"),
    ("deliver this note to the team inbox", None, "email_sender"),
    ("forward this message as an email", None, "email_sender"),
    ("write down what is being said here", "memo.wav", "audio_transcriber"),
    ("turn this voice memo into words", "memo.wav", "audio_transcriber"),
    ("put this speech into written form", "memo.wav", "audio_transcriber"),
    ("capture the action items from this meeting recording", "memo.wav", "meeting_note_taker"),
    ("tell me what this picture contains", "photo.png", "image_classifier"),
    ("identify the objects in this photo", "photo.png", "image_classifier"),
    ("what does this image depict", "photo.png", "image_classifier"),
    ("report the columns and row counts in this csv", "table.csv", "file_analyzer"),
    ("give me an overview of this data file", "table.csv", "file_analyzer"),
)


def _actual(router: Router, query: str, attachment_path: str | None) -> str:
    outcome = router.route(query, attachment_path=attachment_path)
    if outcome.plan is None:
        return f"no plan ({outcome.break_reason.value if outcome.break_reason else '?'})"
    return outcome.plan.steps[-1].agent


def test_held_out_paraphrases_match_recorded_accuracy(tmp_path: Path) -> None:
    (tmp_path / "memo.wav").write_bytes(b"RIFF....")
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "table.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    repo = AgentRepository(InMemoryGraph(), builtin_registry())
    seed_repository(repo, MAINTAINER)
    router = Router(repo)

    actuals: list[str] = []
    correct = 0
    for query, attachment, expected in HELD_OUT:
        path = str(tmp_path / attachment) if attachment else None
        actual = _actual(router, query, path)
        actuals.append(actual)
        correct += actual == expected
        # determinism: the same request routes the same way twice
        assert _actual(router, query, path) == actual

    total = len(HELD_OUT)
    print(f"\n[routing-heldout] {correct}/{total} = {correct / total:.3f} correct (memory backend)")

    doc = EVAL_DOC.read_text(encoding="utf-8")
    recorded = re.search(r"Held-out accuracy:\s*\*\*(\d+)/(\d+)", doc)
    assert recorded is not None, "docs/ROUTING_EVAL.md must record 'Held-out accuracy: **N/20'"
    assert (int(recorded.group(1)), int(recorded.group(2))) == (correct, total), (
        "docs/ROUTING_EVAL.md accuracy is stale; update it to "
        f"{correct}/{total}"
    )
    for i, (actual, (_, _, _)) in enumerate(zip(actuals, HELD_OUT, strict=True), 1):
        assert f"| {i} |" in doc
