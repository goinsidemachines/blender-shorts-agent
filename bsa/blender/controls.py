"""Animated values for geometry-nodes: keys on a control Empty, read inside the node group.

Do not key a geometry-nodes modifier's own input sockets. On Blender 5.3 such keys (data path
`modifiers["X"].properties.inputs["Socket_1"]["value"]`) are written correctly, but a frame-by-frame animation render
(what `bsa.blender.render.render_range` does) shows every frame as frame 1: the modifier is not re-evaluated when
the frame changes. Keys on an object's location are always re-evaluated, so the animated value lives on a control
Empty and the node group reads it with an Object Info node.

Never key the control's scale: Object Info's Scale output comes from decomposing the matrix and loses the sign (a
negative value comes back positive). Location keeps the sign, so a control carries up to three signed floats, one per
location axis; more channels use more Empties (`CHANNELS_PER_CONTROL`).

    chans = controls.ControlSet("Wave", ["Lo", "Hi", "Phase", "Amp"], collection=coll)  # 2 Empties
    chans.key("Phase", frames, values)              # any signed floats, any frames
    out = chans.wire(node_group)                    # {"Lo": socket, "Hi": socket, ...} inside the group
    chans.bind(modifier, node_group)                # point the modifier at the Empties

Every `ControlSet` of the same channel names can share one node group: wire it once, bind each modifier to its own
set. The pure parts (`n_controls`, `slot`, `control_name`, `socket_name`) need no Blender; bpy is imported on use.
"""

from __future__ import annotations

from collections.abc import Sequence

CHANNELS_PER_CONTROL = 3  # one per location axis (X, Y, Z)
AXES = "XYZ"
SOCKET_PREFIX = "Control"  # the node group's Object inputs are "Control 1", "Control 2", ...


def n_controls(n_channels: int) -> int:
    """How many Empties carry `n_channels` channels (three each).

    >>> n_controls(0), n_controls(3), n_controls(4), n_controls(7)
    (0, 1, 2, 3)
    """
    return -(-n_channels // CHANNELS_PER_CONTROL)


def slot(channels: Sequence[str], name: str) -> tuple[int, int]:
    """(empty number, axis 0..2) holding channel `name`: channels fill each Empty's X, Y, Z in order.

    >>> slot(["a", "b", "c", "d"], "d")
    (1, 0)
    """
    names = list(channels)
    if len(set(names)) != len(names):
        raise ValueError(f"channel names must be unique: {names}")
    if name not in names:
        raise KeyError(f"no channel {name!r}; channels are {names}")
    return divmod(names.index(name), CHANNELS_PER_CONTROL)


def control_name(base: str, i: int, count: int) -> str:
    """Empty name: `base control`, or `base control 2` ... when there are several."""
    return f"{base} control" if count == 1 else f"{base} control {i + 1}"


def socket_name(i: int, prefix: str = SOCKET_PREFIX) -> str:
    """Name of the node group's i-th (0-based) Object input: 'Control 1', 'Control 2'..."""
    return f"{prefix} {i + 1}"


def control(name: str, collection=None):
    """A new Empty at the origin to carry animated channels (linked to `collection`, else the active scene's)."""
    import bpy

    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = "PLAIN_AXES"
    ob.empty_display_size = 0.01
    (collection or bpy.context.scene.collection).objects.link(ob)
    return ob


def key_channel(ctl, axis: int, frames: Sequence[float], values: Sequence[float]) -> None:
    """Key the Empty's location on `axis` (0 X, 1 Y, 2 Z): one linear key per (frame, value), any sign. The location
    is put back to its value before keying. Key every frame the value changes on for exact results."""
    from bsa.blender.bake import _fcurves

    if len(frames) != len(values):
        raise ValueError(f"{len(frames)} frames but {len(values)} values")
    if not 0 <= axis < CHANNELS_PER_CONTROL:
        raise ValueError(f"axis must be 0, 1 or 2, not {axis}")
    before = ctl.location[axis]
    for f, v in zip(frames, values, strict=True):
        ctl.location[axis] = float(v)
        ctl.keyframe_insert("location", index=axis, frame=float(f))
    for fc in _fcurves(ctl.animation_data.action):
        if fc.data_path == "location" and fc.array_index == axis:
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
    ctl.location[axis] = before


class ControlSet:
    """Named animated channels spread over as many control Empties as needed (three signed floats each)."""

    def __init__(self, base: str, channels: Sequence[str], collection=None):
        self.base = base
        self.channels = list(channels)
        if len(set(self.channels)) != len(self.channels):
            raise ValueError(f"channel names must be unique: {self.channels}")
        count = n_controls(len(self.channels))
        self.empties = [control(control_name(base, i, count), collection) for i in range(count)]

    def key(self, name: str, frames: Sequence[float], values: Sequence[float]) -> None:
        """Key channel `name` over `frames` (see key_channel)."""
        i, axis = slot(self.channels, name)
        key_channel(self.empties[i], axis, frames, values)

    def wire(self, node_group, prefix: str = SOCKET_PREFIX) -> dict:
        """Add to `node_group` an Object input per Empty ('Control 1', ...) and, per Empty, an Object Info node
        (original space) into a Separate XYZ node. Returns {channel name: output socket} to link wherever the value is
        used. Call once per node group: the same group serves every ControlSet with these channel names."""
        iface = node_group.interface
        nodes, links = node_group.nodes, node_group.links
        have = {it.name for it in iface.items_tree}
        for i in range(len(self.empties)):
            if socket_name(i, prefix) not in have:
                iface.new_socket(socket_name(i, prefix), in_out="INPUT", socket_type="NodeSocketObject")
        gin = nodes.new("NodeGroupInput")  # created after the sockets, so it shows them
        out = {}
        for i in range(len(self.empties)):
            info = nodes.new("GeometryNodeObjectInfo")
            info.transform_space = "ORIGINAL"
            sep = nodes.new("ShaderNodeSeparateXYZ")
            links.new(gin.outputs[socket_name(i, prefix)], info.inputs["Object"])
            links.new(info.outputs["Location"], sep.inputs[0])
            for j, name in enumerate(self.channels[i * CHANNELS_PER_CONTROL : (i + 1) * CHANNELS_PER_CONTROL]):
                out[name] = sep.outputs[j]
        return out

    def bind(self, modifier, node_group, prefix: str = SOCKET_PREFIX) -> None:
        """Set the modifier's Object inputs ('Control 1', ...) to this set's Empties."""
        ids = {it.name: it.identifier for it in node_group.interface.items_tree if it.item_type == "SOCKET"}
        for i, ob in enumerate(self.empties):
            modifier.properties.inputs[ids[socket_name(i, prefix)]]["value"] = ob
