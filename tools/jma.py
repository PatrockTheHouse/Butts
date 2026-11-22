"""Halo JMA-family animation files (JMA/JMM/JMO/JMR/JMT/JMZ; the text format of the Halo CE/MCC mod tools).

    import jma
    a = jma.read(path)             # a.nodes, a.parents, a.frame_count, a.frame_rate, a.pos[F,N,3], a.rot[F][N] (Rot)
    w_pos, w_rot = a.world(f)      # world transforms of frame f (node order), Halo axes: x forward, y left, z up

Layout (version 16392, the one the Halo Animation Repository exports in; 16390/16391 only differ in the header):
    version
    frame count
    frame rate
    actor count, then one actor name
    node count
    node list checksum
    per node: name / first child index / next sibling index           (-1 = none)
    per frame, per node: "x y z" translation / "i j k w" rotation / scale
Translations are in JMS units (100 per Halo world unit; 1 world unit = 10 ft, so 1 unit = 3.048 cm). The kinds
differ only in how the game uses them (JMA with root movement, JMM none, JMO overlay, JMR replacement, JMT turn);
the file body is the same. The per-node transforms are local to the parent, pelvis (node 0) in world space.
The quaternions are stored inverted (3ds Max convention; the Blender Halo toolset inverts them on import, and so
does this reader): with them as written the feet end up above the head.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation as Rot

JMS_UNIT_M = 0.03048                       # metres per JMS unit
# Halo world axes (x forward, y left, z up) -> FromSoft world axes (x right, y up, z backward): a proper rotation
HALO_TO_FROMSOFT = Rot.from_matrix(np.array([[0.0, -1.0, 0.0],
                                             [0.0, 0.0, 1.0],
                                             [-1.0, 0.0, 0.0]]))


@dataclass
class Animation:
    version: int
    frame_count: int
    frame_rate: float
    nodes: list[str]
    parents: list[int]
    pos: np.ndarray            # (F, N, 3) local translation, JMS units
    rot: list                  # [F][N] scipy Rot, local
    scale: np.ndarray          # (F, N)

    @property
    def index(self) -> dict[str, int]:
        return {n: i for i, n in enumerate(self.nodes)}

    def order(self) -> list[int]:
        """Node indices parent-first."""
        out, roots = [], [i for i, p in enumerate(self.parents) if p < 0]
        children = {i: [] for i in range(len(self.nodes))}
        for i, p in enumerate(self.parents):
            if p >= 0:
                children[p].append(i)

        def visit(i):
            out.append(i)
            for c in children[i]:
                visit(c)
        for r in roots:
            visit(r)
        return out

    def world(self, f: int):
        """(pos[N,3] in JMS units, rot[N]) of frame f in Halo world axes."""
        n = len(self.nodes)
        wp = np.zeros((n, 3))
        wr = [None] * n
        for i in self.order():
            p = self.parents[i]
            if p < 0:
                wp[i], wr[i] = self.pos[f, i], self.rot[f][i]
            else:
                wp[i] = wp[p] + wr[p].apply(self.pos[f, i])
                wr[i] = wr[p] * self.rot[f][i]
        return wp, wr


def read(path: Path | str) -> Animation:
    lines = [ln.strip() for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines()]
    lines = [ln for ln in lines if ln != ""]
    k = 0

    def take():
        nonlocal k
        v = lines[k]
        k += 1
        return v
    version = int(take())
    frame_count = int(take())
    frame_rate = float(take())
    actor_count = int(take())
    for _ in range(actor_count):
        take()
    node_count = int(take())
    take()                                   # node list checksum
    nodes, first_child, next_sibling = [], [], []
    for _ in range(node_count):
        nodes.append(take())
        first_child.append(int(take()))
        next_sibling.append(int(take()))
    parents = [-1] * node_count
    for i in range(node_count):
        c = first_child[i]
        while c != -1:
            parents[c] = i
            c = next_sibling[c]
    pos = np.zeros((frame_count, node_count, 3))
    rot = [[None] * node_count for _ in range(frame_count)]
    scale = np.ones((frame_count, node_count))
    for f in range(frame_count):
        for i in range(node_count):
            pos[f, i] = [float(x) for x in take().split()]
            q = [float(x) for x in take().split()]      # i j k w, stored inverted
            rot[f][i] = Rot.from_quat([q[0], q[1], q[2], q[3]]).inv()
            scale[f, i] = float(take())
    return Animation(version, frame_count, frame_rate, nodes, parents, pos, rot, scale)


def describe(a: Animation) -> str:
    return (f"v{a.version} {a.frame_count} frames @ {a.frame_rate:g} fps, {len(a.nodes)} nodes; root {a.nodes[0]}; "
            f"pelvis path {a.pos[0, 0].round(2).tolist()} -> {a.pos[-1, 0].round(2).tolist()} JMS units")


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(Path(p).name + ":", describe(read(p)))
