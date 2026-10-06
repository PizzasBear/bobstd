from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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
    "rm -f $out && $arbin $arflags $out $in",
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
arflags = ar["arflags"]
ldlibs = ld["ldlibs"]

cflags.set(["-fdiagnostics-color=always"])
asflags.set([])
ldflags.set([])
arflags.set(["crs"])
ldlibs.set([])

_ccbinvar = ccbin
_asbinvar = asbin
_ldbinvar = ldbin
_arbinvar = arbin
_ldlibsvar = ldlibs

_bundle_objects: ScopedValue[list[RuleInput.Type]] = ScopedValue([])


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

    def scope(
        self,
    ) -> Scope:
        """Return a scope where the bundle is used."""

        return (
            cflags.add([*self.cflags])
            | asflags.add([*self.asflags])
            | ldflags.add([*self.ldflags])
            | ldlibs.add(self.ldlibs)
            | asm.get(required=True).order_only.add(self.order_only)
            | cc.get(required=True).order_only.add(self.order_only)
            | _bundle_objects.add(self.objects)
        )

    @staticmethod
    def sum(bundles: list["Bundle"] | None) -> "Bundle":
        return sum((bundles or []) + default_bundles.get(required=True), Bundle())


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

    with Bundle.sum(bundles).scope():
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


def _inputs_with_sources(
    name: str,
    sources: list[RuleInput.Type],
    inputs: None | list[RuleInput.Type] = None,
    bundles: None | list[Bundle] = None,
    implicit: None | list[RuleInput.Type] = None,
    order_only: None | list[RuleInput.Type] = None,
) -> list[RuleInput.Type]:
    with Bundle.sum(bundles).scope():
        return (
            [
                object(
                    source,
                    implicit=implicit,
                    order_only=order_only,
                    name_transform=lambda p: Path("obj") / name / p,
                )
                for source in sources
            ]
            + (inputs or [])
            + _bundle_objects.get(required=True)
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

    inputs = _inputs_with_sources(
        name=name,
        sources=sources,
        inputs=inputs,
        bundles=bundles,
        implicit=implicit,
        order_only=order_only,
    )

    with (
        Bundle.sum(bundles).scope(),
        _ldlibsvar.add([*ldlibs] if ldlibs is not None else []),
    ):
        ldlibs_value = _ldlibsvar.get()
        assert (
            not isinstance(ldlibs_value, str)
            and not isinstance(ldlibs_value, Path)
            and not isinstance(ldlibs_value, FileTarget)
            and not isinstance(ldlibs_value, PhonyTarget)
        )

        return ld.build(
            name,
            inputs=inputs,
            implicit=ldlibs_value,
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

    inputs = _inputs_with_sources(
        name=name,
        sources=sources,
        inputs=inputs,
        bundles=bundles,
        implicit=implicit,
        order_only=order_only,
    )

    with Bundle.sum(bundles).scope():
        return ar.build(
            name + ".a",
            inputs=inputs,
            implicit_outputs=implicit_outputs,
        )


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
        cflags.add([*public_cflags]),
        asflags.add([*public_asflags]),
        ldflags.add([*public_ldflags]),
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

    return sum(
        bundles or [],
        Bundle(
            ldlibs=[library],
            cflags=public_cflags,
            asflags=public_asflags,
            ldflags=public_ldflags,
        ),
    )


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
        _ccbinvar.set(ccbin)
        | _asbinvar.set(asbin)
        | _ldbinvar.set(ldbin)
        | _arbinvar.set(arbin)
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
    "arflags",
    "ldlibs",
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
