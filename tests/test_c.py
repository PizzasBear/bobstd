import subprocess
from pathlib import Path
from textwrap import dedent

from bob.commands.build import build


def test_hello_world(unique_tmp_path: Path, bobfile: Path, builddir: Path) -> None:
    bobfile.write_text(
        dedent("""
            from bob.prelude import *
            import bobstd.c as c
            
            c.toolchain("gcc", "ar")

            c.binary("example", sources=["example.c"])
    """)
    )

    (unique_tmp_path / "example.c").write_text(
        dedent("""
            #include <stdio.h>

            int main() {
                puts("Hello World!");   
            }       
        """)
    )

    build(builddir, bobfile)

    assert (
        subprocess.run(
            builddir / "example", check=True, stdout=subprocess.PIPE, text=True
        ).stdout
        == "Hello World!\n"
    )
