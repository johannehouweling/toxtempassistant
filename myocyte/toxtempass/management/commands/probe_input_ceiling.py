"""Find out whether a deployment's input ceiling moves with the output request.

``gpt-5.4-mini`` advertises a 400k context window but rejected a 275,692-token
request with "Input tokens exceed the configured limit of 272000 tokens". 272k
plus the model's 128k max output is exactly 400k, which leaves two readings:

* **static** -- the deployment reserves 128k for output permanently, so the
  input ceiling is 272k no matter how little output you ask for. Capping output
  frees no input.
* **per-request** -- the window is shared, so asking for 1k of output would
  leave ~399k for input. Capping output frees a great deal.

The difference decides whether setting an output cap is worth anything, and it
cannot be read off the documentation. This sends one deliberately oversized
request with a small explicit output cap and reports which way it went.

Costs real money: roughly 285k input tokens, about USD 0.21 on gpt-5.4-mini.
Use --dry-run to see the request without sending it.

    manage.py probe_input_ceiling --model 4:GPT54MINI --dry-run

Drop --dry-run to actually send it.
"""

from __future__ import annotations

import re
from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError
from langchain_core.messages import HumanMessage, SystemMessage

from toxtempass.azure_registry import get_model as get_azure_model
from toxtempass.filehandling import estimate_token_count
from toxtempass.llm import get_llm_for_endpoint

# Neutral filler. Real prose, so the tokenizer behaves as it would on a document.
FILLER = (
    "The assay was performed under standard conditions and the results were "
    "recorded for each replicate in the series. "
)


def _build_input(target_tokens: int) -> str:
    """Return filler text of at least ``target_tokens`` estimated tokens.

    Measured rather than multiplied: tokens merge across repeat boundaries, so
    ``copies * tokens_per_copy`` overshoots by several percent, and the point of
    this probe is that the size is trustworthy.
    """
    per_copy = max(estimate_token_count(FILLER), 1)
    copies = max(1, target_tokens // per_copy)
    text = FILLER * copies
    # Converge upward; each pass closes most of the remaining gap.
    for _ in range(10):
        actual = estimate_token_count(text)
        if actual >= target_tokens:
            return text
        shortfall = target_tokens - actual
        text += FILLER * max(1, shortfall // per_copy)
    return text


class Command(BaseCommand):
    help = "Send one oversized request to see whether the input ceiling is static."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the command's options."""
        parser.add_argument(
            "--model",
            required=True,
            help='Deployment to probe, as "index:tag" (e.g. "4:GPT54MINI").',
        )
        parser.add_argument(
            "--input-tokens",
            type=int,
            default=285_000,
            help="Approximate input size to send (default: 285000).",
        )
        parser.add_argument(
            "--max-output",
            type=int,
            default=1_000,
            help="Explicit output cap to request (default: 1000).",
        )
        parser.add_argument(
            "--reasoning",
            action="store_true",
            help=(
                "Instead of the ceiling probe, ask one realistic question and "
                "report how many of the billed output tokens were reasoning. "
                "Costs a fraction of a cent."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be sent, send nothing, spend nothing.",
        )

    def _measure_reasoning(self, index: int, tag: str, entry: object) -> None:
        """Ask one realistic question and report the reasoning share of output.

        ``max_completion_tokens`` budgets visible output and reasoning together,
        so the cap cannot be sized from stored answer text. The API reports the
        split; one call measures it.
        """
        llm = get_llm_for_endpoint(index, tag, temperature=0)
        context = (
            "The assay uses HepG2 cells cultured in DMEM supplemented with 10% "
            "fetal bovine serum at 37 degrees Celsius under 5% CO2. Cells were "
            "seeded at 10,000 per well in 96-well plates and exposed for 24 "
            "hours. Viability was measured with a resazurin reduction assay and "
            "read on a fluorescence plate reader."
        )
        messages = [
            SystemMessage(content="Context for this question:\n" + context),
            HumanMessage(
                content=(
                    "Describe the cell culture conditions used in this assay, "
                    "including medium, supplements, temperature and atmosphere."
                )
            ),
        ]
        self.stdout.write("asking one question...")
        response = llm.invoke(messages)

        usage = getattr(response, "usage_metadata", None) or {}
        details = usage.get("output_token_details") or {}
        output = usage.get("output_tokens") or 0
        reasoning = details.get("reasoning") or 0
        visible = estimate_token_count(str(response.content or ""))

        self.stdout.write(self.style.SUCCESS("\nusage"))
        self.stdout.write(f"  billed output tokens   {output:,}")
        self.stdout.write(f"  of which reasoning     {reasoning:,}")
        self.stdout.write(f"  visible answer (est.)  {visible:,}")
        if reasoning and visible:
            self.stdout.write(
                self.style.WARNING(
                    f"\nReasoning cost {reasoning / max(visible, 1):.1f}x the "
                    "visible answer. An output cap has to cover both, so size "
                    "it from the billed figure, not from stored answer text."
                )
            )
        elif output:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nNo reasoning tokens reported: billed output is the "
                    "visible answer, so the cap can be sized from answer "
                    "length directly."
                )
            )

    def handle(self, *args: object, **options: object) -> None:
        """Send the probe and report whether the ceiling moved."""
        model_key = str(options["model"])
        try:
            index_s, tag = model_key.split(":", 1)
            resolved = get_azure_model(int(index_s), tag)
        except (ValueError, TypeError) as exc:
            raise CommandError(f"Could not parse --model {model_key!r}: {exc}") from exc
        if resolved is None:
            raise CommandError(f"No deployment {model_key!r} in the registry.")
        _endpoint, entry = resolved

        if options["reasoning"]:
            self._measure_reasoning(int(index_s), tag, entry)
            return

        target = int(options["input_tokens"])
        max_output = int(options["max_output"])
        text = _build_input(target)
        estimated = estimate_token_count(text)

        self.stdout.write(self.style.HTTP_INFO("probe"))
        self.stdout.write(f"  deployment      {model_key} ({entry.model_id})")
        self.stdout.write(f"  api             {entry.api}")
        self.stdout.write(f"  input           ~{estimated:,} tokens (estimated)")
        self.stdout.write(f"  output cap      {max_output:,}")
        self.stdout.write(
            "  note            tiktoken undercounts for o200k models, so the "
            "real count is higher"
        )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\ndry run: nothing sent."))
            return

        llm = get_llm_for_endpoint(int(index_s), tag, temperature=0)
        # Anthropic uses max_tokens; GPT-5 and GPT-4o on Azure reject that name
        # and require max_completion_tokens.
        cap_name = "max_tokens" if entry.api == "anthropic" else "max_completion_tokens"
        self.stdout.write(f"  parameter       {cap_name}")
        bound = llm.bind(**{cap_name: max_output})
        messages = [
            SystemMessage(content=text),
            HumanMessage(content="Reply with the single word: ok"),
        ]

        self.stdout.write("\nsending...")
        try:
            response = bound.invoke(messages)
        except Exception as exc:  # noqa: BLE001 - the error text is the result
            message = str(exc)
            self.stdout.write(self.style.ERROR("\nREJECTED"))
            self.stdout.write(f"  {message[:600]}")
            reported = re.search(r"resulted in ([\d,]+) tokens", message)
            limit = re.search(r"limit of ([\d,]+) tokens", message)
            if limit:
                self.stdout.write(
                    self.style.WARNING(
                        f"\nVERDICT: STATIC. The ceiling stayed at "
                        f"{limit.group(1)} tokens even with the output capped at "
                        f"{max_output:,}, so capping output frees no input."
                    )
                )
                if reported:
                    self.stdout.write(
                        f"  (the request really was {reported.group(1)} tokens, "
                        "against an estimate of "
                        f"{estimated:,} -- the gap is the tokenizer difference)"
                    )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "\nInconclusive: rejected, but not for the input limit. "
                        "Read the message above."
                    )
                )
            return

        usage = getattr(response, "usage_metadata", None) or {}
        actual_input = usage.get("input_tokens")
        self.stdout.write(self.style.SUCCESS("\nACCEPTED"))
        self.stdout.write(f"  input tokens billed  {actual_input or 'not reported'}")
        self.stdout.write(f"  reply                {str(response.content)[:80]!r}")
        if actual_input and actual_input > 272_000:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nVERDICT: PER-REQUEST. {actual_input:,} input tokens were "
                    f"accepted -- above the 272,000 seen before -- because the "
                    f"output was capped at {max_output:,}. Capping output does "
                    "free input, and the cap is worth setting."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "\nInconclusive: accepted, but the billed input was not above "
                    "272,000. Re-run with a larger --input-tokens."
                )
            )
