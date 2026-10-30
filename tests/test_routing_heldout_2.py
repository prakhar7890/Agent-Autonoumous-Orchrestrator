"""Second held-out routing evaluation (memory backend).

20 further paraphrased requests that appear in neither any seed agent's `example_queries` nor the
first held-out set. They were written once and **not used to tune the router** (routing logic and
seed exemplars are unchanged). Like `test_routing_heldout.py`, this measures routing accuracy and
checks that the number recorded in `docs/ROUTING_EVAL.md` still matches the live run, so the doc
cannot silently drift.
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
HELD_OUT_2: tuple[tuple[str, str | None, str], ...] = (
    ("condense this long article into a few sentences", None, "text_summarizer"),
    ("pull out the main takeaway from this paragraph", None, "text_summarizer"),
    ("give me the short version of this write-up", None, "text_summarizer"),
    ("reduce this document to its key ideas", None, "text_summarizer"),
    ("recap the important parts of this text", None, "text_summarizer"),
    ("sketch a picture of a lighthouse at night", None, "image_generator"),
    ("design an image of a robot chef", None, "image_generator"),
    ("paint a scene of a quiet forest", None, "image_generator"),
    ("email the weekly update to my colleagues", None, "email_sender"),
    ("send this summary by mail to the manager", None, "email_sender"),
    ("dispatch this note to the team", None, "email_sender"),
    ("convert this audio clip into text", "memo.wav", "audio_transcriber"),
    ("transcribe what is on this recording", "memo.wav", "audio_transcriber"),
    ("write out the words from this voice note", "memo.wav", "audio_transcriber"),
    ("summarize the meeting from this recording", "memo.wav", "meeting_note_taker"),
    ("make meeting notes from this call recording", "memo.wav", "meeting_note_taker"),
    ("label the contents of this image", "photo.png", "image_classifier"),
    ("what objects can you see in this picture", "photo.png", "image_classifier"),
    ("categorize what this image shows", "photo.png", "image_classifier"),
    ("report the statistics for this data file", "table.csv", "file_analyzer"),
)


def _actual(router: Router, query: str, attachment_path: str | None) -> str:
    outcome = router.route(query, attachment_path=attachment_path)
    if outcome.plan is None:
        return f"no plan ({outcome.break_reason.value if outcome.break_reason else '?'})"
    return outcome.plan.steps[-1].agent


def test_second_held_out_set_matches_recorded_accuracy(tmp_path: Path) -> None:
    (tmp_path / "memo.wav").write_bytes(b"RIFF....")
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "table.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    repo = AgentRepository(InMemoryGraph(), builtin_registry())
    seed_repository(repo, MAINTAINER)
    router = Router(repo)

    actuals: list[str] = []
    correct = 0
    for query, attachment, expected in HELD_OUT_2:
        path = str(tmp_path / attachment) if attachment else None
        actual = _actual(router, query, path)
        actuals.append(actual)
        correct += actual == expected
        # determinism: the same request routes the same way twice
        assert _actual(router, query, path) == actual

    total = len(HELD_OUT_2)
    print(f"\n[routing-heldout-2] {correct}/{total} = {correct / total:.3f} correct (memory backend)")

    doc = EVAL_DOC.read_text(encoding="utf-8")
    recorded = re.search(r"held-out set 2 accuracy:\s*\*\*(\d+)/(\d+)", doc, re.IGNORECASE)
    assert recorded is not None, "docs/ROUTING_EVAL.md must record 'Held-out set 2 accuracy: **N/20'"
    assert (int(recorded.group(1)), int(recorded.group(2))) == (correct, total), (
        "docs/ROUTING_EVAL.md set-2 accuracy is stale; update it to "
        f"{correct}/{total}"
    )
    for i, (actual, (_, _, _)) in enumerate(zip(actuals, HELD_OUT_2, strict=True), 1):
        assert f"| {i} |" in doc
