import logging
import subprocess
from pathlib import Path

_LOG = logging.getLogger(__name__)

cwd = Path(__file__).parent.parent
out_folder = cwd / "auto_generated_html"
if not out_folder.exists():
    out_folder.mkdir()
for md_file in list(cwd.glob("**/*.md")):
    # Define the output file name (you can adjust the extension or output directory)
    output_file = (
        out_folder / md_file.with_suffix(".html").name
    )  # Convert markdown to HTML

    # Run pandoc using subprocess
    try:
        subprocess.run(  # noqa: S603,
            [  # noqa: S607
                "pandoc",
                str(md_file),
                f"--template={cwd / 'dependencies/GitHub.html5'}",
                # The output is included inside modals on every page, so demote
                # headings: a stray <h1> would compete with the page's own <h1>.
                "--shift-heading-level-by=1",
                "-o",
                str(output_file),
            ],
            check=True,
        )
        _LOG.info(f"Successfully converted {md_file} to {output_file}")
    except subprocess.CalledProcessError as e:
        _LOG.warning(f"Error processing {md_file}: {e}")
