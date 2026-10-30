# Manager demo script

1. Show the agent catalog and the immutable version lineage for `text_summarizer`.
2. Submit a text request and display the selected exact version, plan reasons, output, and
   sanitized execution trace.
3. Submit an attachment request and show artifact metadata and the linked result.
4. Trigger a bounded failure with an unavailable or invalid input and show the clarification
   breakpoint without retrying beyond configured limits.
5. Show a feedback proposal, its candidate checksum, evaluation gate, and the fact that it
   cannot activate until a named human approves it.

Use offline stubs for the demo. Label all historical live-provider evidence separately.
