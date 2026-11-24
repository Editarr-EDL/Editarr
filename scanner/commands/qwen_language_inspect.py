"""Run one bounded Qwen inspection request received over stdin."""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from traceback import print_exc


def main() -> int:
    """Keep model diagnostics on stderr and return one JSON response on stdout."""
    try:
        with redirect_stdout(sys.stderr):
            from scanner.evaluation.qwen_language_inspection import (
                InspectionContext,
                InspectionTestEvidence,
                ask_qwen,
                rerun_inspection,
            )

            request = json.load(sys.stdin)
            evidence = InspectionContext.model_validate(request["evidence"])
            action, text = request["action"], request["text"]
            if action == "ask":
                latest_test = request.get("latest_test")
                result = ask_qwen(
                    evidence,
                    text,
                    latest_test=(
                        InspectionTestEvidence.model_validate(latest_test)
                        if latest_test is not None
                        else None
                    ),
                )
            elif action == "rerun":
                result = rerun_inspection(evidence, text)
            else:
                raise ValueError("Unrecognized Qwen inspection action.")
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)[:500]}))
        return 1
    except Exception:
        print_exc()
        print(json.dumps({"error": "Qwen inspection failed. Check the replay server log."}))
        return 1
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
