from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generator

from bob.prelude import *

p = plugin_scope()

default_cc = Rule(
    "$ccbin -MMD -MT $out -MF $out.d $cflags -c $in -o $out",
    description="CC",
    depfile="$out.d",
    deps="gcc",
    compile_command="$ccbin -MMD -MT $out -MF $out.d $cflags -c $in -o $out",
)
cc = ScopedRule(default_cc)
default_asm = Rule(
    "$asbin -MMD -MT $out -MF $out.d $asflags -c $in -o $out",
    description="AS",
    depfile="$out.d",
    deps="gcc",
    compile_command="$asbin -MMD -MT $out -MF $out.d $asflags -c $in -o $out",
)
asm = ScopedRule(default_asm)
default_ld = Rule(
    "$ldbin $cflags $ldflags -o $out $in $ldlibs",
    description="LD",
)
ld = ScopedRule(default_ld)
default_ar = Rule(
    "rm -f $out && $arbin crs $out $in",
    description="AR",
)
ar = ScopedRule(default_ar)

ccbin = cc["ccbin"]
asbin = asm["asbin"]
ldbin = ld["ldbin"]
arbin = ar["arbin"]

cflags = Variable("cflags", cc, ld)
asflags = asm["asflags"]
ldflags = ld["ldflags"]

cflags.set(["-fdiagnostics-color=always"])
asflags.set([])
ldflags.set([])


@dataclass
class Bundle:
    """A bundle that can be used to create dependent binaries and libraries."""

    objects: list[RuleInput.Type] = field(default_factory=list)
    ldlibs: list[RuleInput.Type] = field(default_factory=list)
    order_only: list[RuleInput.Type] = field(default_factory=list)
    cflags: list[str] = field(default_factory=list)
    asflags: list[str] = field(default_factory=list)
    ldflags: list[str] = field(default_factory=list)

    def __add__(self, other: "Bundle"):
        return Bundle(
            objects=self.objects + other.objects,
            order_only=self.order_only + other.order_only,
            ldlibs=self.ldlibs + other.ldlibs,
            cflags=self.cflags + other.cflags,
            asflags=self.asflags + other.asflags,
            ldflags=self.ldflags + other.ldflags,
        )

    @contextmanager
    def _scope(
        self,
    ) -> Generator[
        tuple[list[RuleInput.Type], list[RuleInput.Type], list[RuleInput.Type]],
        None,
        None,
    ]:
        with (
            cflags.add(self.cflags),  # ty:ignore[invalid-argument-type]
            asflags.add(self.asflags),  # ty:ignore[invalid-argument-type]
            ldflags.add(self.ldflags),  # ty:ignore[invalid-argument-type]
        ):
            yield self.objects, self.ldlibs, self.order_only


default_bundles = ScopedValue[list[Bundle]]([])
"""The default bundles to use for C targets."""

objects: list[FileTarget] = []
"""All built C objects."""


def object(
    source: RuleInput.Type,
    bundles: None | list[Bundle] = None,
    implicit: None | list[RuleInput.Type] = None,
    order_only: None | list[RuleInput.Type] = None,
    implicit_outputs: None | list[str | Path] = None,
    name_transform: Callable[[Path], str | Path] = lambda s: s,
) -> FileTarget:
    """Build a C object created from the given C or Assembly source file."""

    source_path = RuleInput.resolve(source, path_only=True, single=True)

    name = name_transform(Path(RuleInput.id(source_path)).with_suffix(".o"))

    with sum(
        (bundles or []) + default_bundles.get(required=True), Bundle()
    )._scope() as (
        bundle_objects,
        bundle_ldlibs,
        bundle_order_only,
    ):
        order_only = (order_only or []) + bundle_order_only
        if source_path.suffix == ".c":
            result = cc.build(
                name,
                inputs=[source],
                implicit=implicit,
                order_only=order_only,
                implicit_outputs=implicit_outputs,
            )
        elif source_path.suffix == ".S" or source_path.suffix == ".s":
            result = asm.build(
                name,
                inputs=[source],
                implicit=implicit,
                order_only=order_only,
                implicit_outputs=implicit_outputs,
            )
        else:
            raise ValueError(f"Unknown C source extension for file: {source_path}")

    objects.append(result)
    return result


@contextmanager
def _expand(
    name: str,
    sources: list[RuleInput.Type],
    inputs: None | list[RuleInput.Type] = None,
    bundles: None | list[Bundle] = None,
    implicit: None | list[RuleInput.Type] = None,
    order_only: None | list[RuleInput.Type] = None,
) -> Generator[tuple[list[RuleInput.Type], list[RuleInput.Type]], None, None]:
    if inputs is None:
        inputs = []

    total_bundles = sum((bundles or []) + default_bundles.get(required=True), Bundle())

    yield (
        [
            object(
                source,
                implicit=implicit,
                order_only=order_only,
                bundles=[total_bundles],
                name_transform=lambda p: Path("obj") / name / p,
            )
            for source in sources
        ]
        + inputs
        or [] + total_bundles.objects,
        total_bundles.ldlibs,
    )


def binary(
    name: str,
    sources: list[RuleInput.Type],
    inputs: None | list[RuleInput.Type] = None,
    bundles: None | list[Bundle] = None,
    implicit: None | list[RuleInput.Type] = None,
    order_only: None | list[RuleInput.Type] = None,
    implicit_outputs: None | list[str | Path] = None,
    ldlibs: None | list[str] = None,
) -> FileTarget:
    """Build a binary from the given C sources and additional inputs."""

    if ldlibs is None:
        ldlibs = []

    with (
        _expand(
            name=name,
            sources=sources,
            inputs=inputs,
            bundles=bundles,
            implicit=implicit,
            order_only=order_only,
        ) as (inputs, bundle_ldlibs),
        ld["ldlibs"].set(ldlibs + bundle_ldlibs),
    ):
        return ld.build(
            name,
            inputs=inputs,
            implicit=bundle_ldlibs,
            implicit_outputs=implicit_outputs,
        )


def static_library(
    name: str,
    sources: list[RuleInput.Type],
    inputs: None | list[RuleInput.Type] = None,
    bundles: None | list[Bundle] = None,
    implicit: None | list[RuleInput.Type] = None,
    order_only: None | list[RuleInput.Type] = None,
    implicit_outputs: None | list[str | Path] = None,
) -> FileTarget:
    """Build a static archive from the given C sources and additional inputs."""

    with _expand(
        name=name,
        sources=sources,
        inputs=inputs,
        bundles=bundles,
        implicit=implicit,
        order_only=order_only,
    ) as (inputs, bundle_ldlibs):
        return ar.build(name + ".a", inputs=inputs, implicit_outputs=implicit_outputs)


def static_library_bundle(
    name: str,
    sources: list[RuleInput.Type],
    inputs: None | list[RuleInput.Type] = None,
    bundles: None | list[Bundle] = None,
    public_cflags: None | list[str] = None,
    public_asflags: None | list[str] = None,
    public_ldflags: None | list[str] = None,
    implicit: None | list[RuleInput.Type] = None,
    order_only: None | list[RuleInput.Type] = None,
    implicit_outputs: None | list[str | Path] = None,
) -> Bundle:
    """Build a static archive from the given C sources and additional inputs and return a bundle which lets other binaries and libraries use this library."""

    if public_cflags is None:
        public_cflags = []
    if public_asflags is None:
        public_asflags = []
    if public_ldflags is None:
        public_ldflags = []

    with (
        cflags.add(public_cflags),  # ty:ignore[invalid-argument-type]
        asflags.add(public_asflags),  # ty:ignore[invalid-argument-type]
        ldflags.add(public_ldflags),  # ty:ignore[invalid-argument-type]
    ):
        library = static_library(
            name=name,
            sources=sources,
            inputs=inputs,
            bundles=bundles,
            implicit=implicit,
            order_only=order_only,
            implicit_outputs=implicit_outputs,
        )

    ldlibs: list[RuleInput.Type] = [library] + [
        ldlib
        for bundle in (bundles or []) + default_bundles.get(required=True)
        for ldlib in bundle.ldlibs
    ]

    return Bundle(
        ldlibs=ldlibs,
        cflags=public_cflags,
        asflags=public_asflags,
        ldflags=public_ldflags,
    )


ccbinvar = ccbin
asbinvar = asbin
ldbinvar = ldbin
arbinvar = arbin


def toolchain(
    ccbin: RuleInput.Type,
    arbin: RuleInput.Type,
    asbin: None | RuleInput.Type = None,
    ldbin: None | RuleInput.Type = None,
) -> Scope:
    """
    Return a scope using the given C toolchain.
    The `ccbin` is used for `asbin` and `ldbin` if they aren't provided.
    """

    if asbin is None:
        asbin = ccbin

    if ldbin is None:
        ldbin = ccbin

    return (
        ccbinvar.set(ccbin)
        | asbinvar.set(asbin)
        | ldbinvar.set(ldbin)
        | arbinvar.set(arbin)
    )


def add_include_path(
    path: RuleInput.Type, extend_cflags=True, extend_asflags=True
) -> Scope:
    flags: list[RuleInput.Type] = [f"-I{RuleInput.resolve(path, path_only=True)}"]

    scopes: list[Scope] = []
    if extend_cflags:
        scopes.append(cflags.add(flags))
    if extend_asflags:
        scopes.append(asflags.add(flags))

    return ScopeList(scopes)


def add_bundles(*bundles: Bundle) -> Scope:
    return default_bundles.add(list(bundles))


p.close()


__all__ = [
    "cc",
    "asm",
    "ld",
    "ar",
    "ccbin",
    "asbin",
    "ldbin",
    "arbin",
    "default_cc",
    "default_asm",
    "default_ld",
    "default_ar",
    "cflags",
    "asflags",
    "ldflags",
    "Bundle",
    "object",
    "binary",
    "static_library",
    "static_library_bundle",
    "toolchain",
    "add_include_path",
    "add_bundles",
    "objects",
]
