"""Command-line interface.

Thin by design: parse arguments, call the runner, render. No scientific logic lives here —
if a decision can be made in the CLI, it is in the wrong place.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from dendro_inspector.config import Adapter, AppConfig, ProviderConfig, Role, load_config
from dendro_inspector.evaluation.reporting import render_json, render_text
from dendro_inspector.evaluation.runner import run_suite
from dendro_inspector.graph.definition import render_mermaid
from dendro_inspector.observability.logging import configure_logging
from dendro_inspector.observability.trace import write_trace
from dendro_inspector.prompts.library import DomainPromptMissingError, PromptPolicyError
from dendro_inspector.prompts.seal import SEMANTIC_NOTICE, apply_seal, plan_seal
from dendro_inspector.runner import run_case
from dendro_inspector.schemas.input import CaseInput, DeclaredObjectType, ImageRef, Season

app = typer.Typer(
    name="dendro",
    help="Evidence-based dendrology inspection. Not a substitute for professional identification.",
    no_args_is_help=True,
    add_completion=False,
)


def _fake_config(base: AppConfig, scenario: str) -> AppConfig:
    return base.model_copy(
        update={
            "providers": {
                Role.PRIMARY: ProviderConfig(adapter=Adapter.FAKE, scenario=scenario),
                Role.ARBITER: ProviderConfig(adapter=Adapter.FAKE, scenario=scenario),
            }
        }
    )


def _build_case(
    images: list[Path],
    *,
    case_id: str,
    text: str | None,
    claim: str | None,
    field_context: bool,
    location: str | None,
    season: Season,
    habitat: str | None,
    object_type: DeclaredObjectType,
    lang: str,
) -> CaseInput:
    return CaseInput(
        case_id=case_id,
        images=tuple(
            ImageRef(image_id=f"img-{index}", path=path)
            for index, path in enumerate(images, start=1)
        ),
        user_text=text,
        user_claim=claim,
        user_has_field_context=field_context,
        location=location,
        season=season,
        habitat=habitat,
        declared_object_type=object_type,
        metadata={"locale": lang},
    )


@app.command()
def inspect(
    image: Annotated[
        list[Path], typer.Option("--image", "-i", help="Path to an image. Repeat for several.")
    ] = [],
    text: Annotated[
        str | None, typer.Option("--text", help="Free-text context from the user.")
    ] = None,
    claim: Annotated[
        str | None,
        typer.Option("--claim", help='Your own version, in any language: "сосна", "oak".'),
    ] = None,
    field_context: Annotated[
        bool,
        typer.Option(
            "--field-context",
            help="You know things the photo cannot show (foliage out of frame, the fruit, "
            "where it was felled). Blocks aggressive contradiction.",
        ),
    ] = False,
    lang: Annotated[
        str, typer.Option("--lang", help="Output language: uk (default) or en.")
    ] = "uk",
    location: Annotated[
        str | None, typer.Option("--location", help='e.g. "Kyiv Oblast, Ukraine".')
    ] = None,
    season: Annotated[
        Season, typer.Option("--season", help="Season the photo was taken.")
    ] = Season.UNKNOWN,
    habitat: Annotated[str | None, typer.Option("--habitat", help="Habitat description.")] = None,
    object_type: Annotated[
        DeclaredObjectType, typer.Option("--object-type", help="What you believe it is.")
    ] = DeclaredObjectType.UNKNOWN,
    case_id: Annotated[
        str, typer.Option("--case-id", help="Identifier for this run.")
    ] = "cli-case",
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit structured JSON instead of text.")
    ] = False,
    fake: Annotated[
        str | None,
        typer.Option("--fake", help="Run offline against a fixture scenario, e.g. primary-pass."),
    ] = None,
    trace_out: Annotated[
        Path | None, typer.Option("--trace-out", help="Directory to write the JSON trace into.")
    ] = None,
) -> None:
    """Identify the subject(s) in one or more photographs."""
    if not image and not text:
        typer.secho("Provide at least --image or --text.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    config = load_config()
    if fake:
        config = _fake_config(config, fake)
    configure_logging(
        config.observability.log_level, json_output=config.observability.emit_json_logs
    )

    case = _build_case(
        list(image),
        case_id=case_id,
        text=text,
        claim=claim,
        field_context=field_context,
        location=location,
        season=season,
        habitat=habitat,
        object_type=object_type,
        lang=lang,
    )

    try:
        result = asyncio.run(run_case(case, config=config))
    except (DomainPromptMissingError, PromptPolicyError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3) from exc

    response = result.response
    if response is None:
        typer.secho("No response was produced.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if trace_out:
        path = write_trace(result.trace, trace_out)
        typer.secho(f"trace: {path}", fg=typer.colors.BLUE, err=True)

    if as_json:
        payload = {
            "response": response.model_dump(mode="json"),
            "trace": result.trace.model_dump(mode="json"),
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if result.trace.domain_prompt and result.trace.domain_prompt.is_placeholder:
        typer.secho(
            "Warning: running with the PLACEHOLDER domain prompt. "
            "Put your own prompt in prompts/domain/system-prompt.md.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.echo(response.human_readable)


@app.command(name="eval")
def evaluate_suite(
    suite: Annotated[str, typer.Option("--suite", help="Suite directory under evals/.")] = "public",
    as_json: Annotated[bool, typer.Option("--json", help="Emit the report as JSON.")] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show passing assertions too.")
    ] = False,
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the JSON report to this path.")
    ] = None,
) -> None:
    """Run an evaluation suite against deterministic fixtures."""
    config = load_config()
    # A suite run emits the same structured warnings a real run does — which candidates the
    # admission boundary dropped, and why. Without this they reach the last-resort handler
    # bare: no case_id, no subject_id, no redaction.
    configure_logging(
        config.observability.log_level, json_output=config.observability.emit_json_logs
    )
    try:
        report = run_suite(suite, config=config)
    except PromptPolicyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3) from exc
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_json(report), encoding="utf-8")
    typer.echo(render_json(report) if as_json else render_text(report, verbose=verbose))
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def graph(
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the diagram to this path.")
    ] = None,
) -> None:
    """Print the executable agent graph as Mermaid."""
    diagram = render_mermaid()
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(diagram + "\n", encoding="utf-8")
        typer.secho(f"wrote {out}", fg=typer.colors.GREEN, err=True)
    typer.echo(diagram)


@app.command()
def prompt_info() -> None:
    """Show the validated prompt bundle identity and policy compatibility."""
    from dendro_inspector.prompts.library import PromptLibrary

    config = load_config()
    library = PromptLibrary(config.prompts)
    try:
        metadata = library.metadata()
    except (DomainPromptMissingError, PromptPolicyError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3) from exc
    typer.echo(json.dumps(metadata.model_dump(mode="json"), indent=2))


@app.command()
def prompt_seal(
    write: Annotated[
        bool,
        typer.Option("--write", help="Rewrite the manifest. Without it, this is a dry run."),
    ] = False,
) -> None:
    """Re-seal the prompt manifest against the prompt bytes currently on disk.

    Recomputes the domain-prompt and node-prompt SHA-256 values for the
    configured paths and regenerates the configured manifest, so replacing
    the user-managed domain prompt has a supported way back to a runnable
    deployment. The node-prompt file set is taken from the configured root
    as it actually is. The default is a dry run: it prints every hash it
    would change, old -> new, and exits 0, because a manifest that is
    merely out of date is not an error. An unreadable or
    policy-incompatible one is.

    Re-sealing attests bytes, not semantic compatibility. It never
    rewrites `schema_version` or `policy_revision` — those stay pinned to
    what this code supports, so a manifest bound to another policy
    revision is refused rather than quietly upgraded. Whether a changed
    prompt still means what the deterministic policy expects is a review,
    not a hash.
    """
    config = load_config()
    try:
        plan = plan_seal(config.prompts)
    except (DomainPromptMissingError, PromptPolicyError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3) from exc

    typer.echo(f"manifest: {plan.manifest_path}")
    typer.echo(f"domain prompt: {plan.domain_path}")
    typer.echo(f"node prompt root: {plan.node_root}")
    typer.echo(f"schema_version: {plan.sealed.schema_version} (pinned by this code)")
    typer.echo(f"policy_revision: {plan.sealed.policy_revision} (pinned by this code)")
    typer.echo(f"node_prompts.revision: {plan.sealed.node_prompts.revision} (preserved)")

    if plan.up_to_date:
        typer.echo("The manifest already attests these bytes. Nothing to re-seal.")
        return

    typer.echo(f"changes ({len(plan.changes)}):")
    for change in plan.changes:
        typer.echo(f"  {change.render()}")

    if not write:
        typer.echo("Dry run: nothing was written. Re-run with --write to apply.")
        typer.echo(SEMANTIC_NOTICE)
        return

    try:
        metadata = apply_seal(plan, config.prompts)
    except (DomainPromptMissingError, PromptPolicyError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3) from exc

    typer.secho(f"wrote {plan.manifest_path}", fg=typer.colors.GREEN, err=True)
    typer.echo(
        f"revalidated: bundle is {metadata.compatibility_status.value} "
        f"with policy {metadata.policy_revision}."
    )
    typer.echo(SEMANTIC_NOTICE)


if __name__ == "__main__":  # pragma: no cover
    app()
