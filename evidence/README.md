# AWS Builder evidence

Each `bedrock-run-<UTC time>.md` here is one run of the demo's standing
five-turn matchday conversation against the live Fanmoot MCP server, with the
model chain limited to Amazon Bedrock. Its `.jsonl` twin holds every frame the
browser would have received, with tokens and email addresses redacted.

Runs are made with `make evidence`, which uses the same code a visitor's turn
does. It writes nothing unless every turn was answered by a Bedrock model.
Nothing is posted to Fanmoot during a run: the session is never signed in, so
the scenario's final post stops at the server's 401 sign-in challenge.
