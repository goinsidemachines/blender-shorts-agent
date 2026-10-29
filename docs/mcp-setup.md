# Setting up the Blender MCP (optional)

The plugin works without any MCP: in **script mode** Claude runs Blender in the background. The MCP adds **MCP
mode**, where Claude drives your open Blender live and you watch the scene being built. Both modes produce the same
scene from the same build script.

Supported: the **official Blender MCP** from Blender Lab (an add-on named "MCP", maintainer "Blender Lab", plus the
MCP server Claude talks to; it exposes tools such as `execute_blender_code`, scene summaries and screenshots). See
https://www.blender.org/lab/mcp-server/ for the current download and the server's own instructions. It needs
Blender 5.1 or newer.

Not supported: the community `blender-mcp` add-on (it listens on port 9876 by default and exposes different
tools). If you have it installed, disable it so it does not take the port.

## 1. Install the add-on in Blender

1. Download the add-on from the page above and install it: Edit > Preferences > Get Extensions > the menu at the top
   right > Install from Disk..., then pick the downloaded file.
2. Allow online access: Edit > Preferences > System > Network > **Allow Online Access**. The add-on runs a local
   network socket and refuses to start without this (it still only listens on your own computer, `localhost`).
3. Open the add-on's preferences (Edit > Preferences > Add-ons > MCP). You will see **Host** (`localhost`),
   **Port**, **Auto Start**, and a button to start or stop the server.

## 2. Start its server

With **Auto Start** on (the default), the server starts a few seconds after Blender opens. Otherwise press the start
button in the add-on's preferences. The preferences then say "Server is running".

For a Blender with no window (advanced): `blender --background file.blend --online-mode --command blender_mcp --port 9888`.

## 3. Match the port (the step people miss)

The add-on listens on one port; the MCP server Claude starts must connect to **the same port**. Pick a port no other
program uses: 9876 is the add-on's default but other software uses it too, so if anything else is on 9876, use
something like 9888.

1. In Blender: set the add-on's **Port** (for example 9888) and restart its server (stop, then start).
2. In Claude Code: configure the Blender MCP server to connect to that port, as the server's instructions on the
   Blender Lab page describe (a port argument or environment variable in its entry in your MCP configuration,
   `claude mcp add ...` or `.mcp.json`). Restart Claude Code (or run `/mcp` and reconnect) so it picks up the change.

If the two ports differ, the MCP shows as connected in Claude but every Blender call fails or times out.

## 4. Check the connection

In Claude Code, run `/mcp` and check the Blender server is connected. Then ask Claude for a scene summary of the open
file ("what's in my Blender scene?"): it calls a scene-summary tool (for example `get_objects_summary` or
`get_blendfile_summary_datablocks`) and lists the objects in your open file. If that works, MCP mode is ready: the
next time you ask for a short, Claude asks "MCP or script mode?".

## Troubleshooting

- **"Connection refused" / timeouts:** the add-on's server is not running (start it), the ports differ (step 3), or
  another program took the port (pick another, set it in both places).
- **The add-on says online access is disabled:** step 1.2.
- **Calls work but are slow:** the add-on polls for work a few times a second; long builds are fine, but Claude may
  split a big build into several calls.
- Anything else: script mode always works, and Claude falls back to it on its own.
