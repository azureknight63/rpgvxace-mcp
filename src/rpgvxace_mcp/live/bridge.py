"""The Ruby half of the bridge, and how it goes into a scratch copy.

The bridge is one script slot, inserted directly above the slot that calls
`rgss_main`. Above Main, so every class it patches already exists; directly
above, so every other slot keeps its number and a backtrace's `{0042}`
names the same slot in the copy as in the project.

It hooks three places, all by aliasing rather than replacing:

- `Graphics.update` -- once per rendered frame. After the real update the
  bridge counts the frame, reads one request, and, while paused, keeps
  calling the real update (so the window stays responsive) without
  returning to the game. That is what makes a frame steppable.
  `Graphics.wait`, `fadeout` and `fadein` are redefined in Ruby on top of
  it, so their frames are counted and steppable too. `Graphics.transition`
  cannot be: it runs its frames inside the engine.
- `Input.update`, `press?`, `trigger?`, `repeat?`, `dir4`, `dir8` -- a
  queue of virtual key presses, merged with the real keyboard. Nothing is
  sent to the operating system.
- `SceneManager.run` -- a Ruby exception that would have ended the game
  in a modal dialog is written to crash.bin instead, and the bridge keeps
  answering `ping`, `state`, `eval` and `quit` so the wreck can be read.
  `SceneManager.first_scene_class` -- optionally straight to a new game.

Written for Ruby 1.9.2 as RGSS3 ships it, with no `require`: the engine has
no standard library to load. Every name the Python side also uses is
substituted in from `wire`, so the two halves spell each file one way.
"""

from __future__ import annotations

import zlib
from typing import Any

from ..rubytypes import RubyString
from . import wire


# The name the bridge's slot carries in the copy's script list.
BRIDGE_SLOT_NAME = "rpgvxace-mcp live bridge"


# Every op the Ruby side answers. `live_tools` forwards a subset of these;
# `quit` is sent by `stop` and `ping` by `status`.
BRIDGE_OPS = ("ping", "state", "pause", "resume", "step", "input", "set",
              "teleport", "eval", "quit", "passability", "locate", "face")


# The ops the bridge still answers after the game has raised.
CRASH_LOOP_OPS = ("ping", "state", "eval", "quit")


# The keys a virtual press may name: RGSS3's own Input symbols.
KEYS = ("DOWN", "LEFT", "RIGHT", "UP", "A", "B", "C", "X", "Y", "Z", "L", "R",
        "SHIFT", "CTRL", "ALT", "F5", "F6", "F7", "F8", "F9")


# The sections `state` can report, in the order it reports them.
STATE_SECTIONS = ("player", "party", "gold", "items", "switches", "variables",
                  "self_switches", "interpreter", "message")


# How many entries one section of `state` lists before it says it stopped.
MAX_STATE_ENTRIES = 2000


# Longest string the bridge puts in a reply: a message line, an `inspect`.
MAX_BRIDGE_TEXT = 4000


# How long a teleport may take to land before the bridge gives up on it.
TELEPORT_FRAMES = 600


# The most cells one `passability` read asks the game about. Each cell is
# four calls to the game's own `passable?`, and each of those looks at every
# event on the map, all inside one frame: a bounded read keeps that frame
# short. A larger map is read in a window around the walk.
MAX_PASS_CELLS = 4096


_RUBY = r'''#==============================================================================
# ** rpgvxace-mcp live bridge
#------------------------------------------------------------------------------
#  Injected by rpgvxace-mcp into a SCRATCH COPY of this project so an MCP
#  client can drive a test run. It is never written into the project itself.
#  Once a frame it reads one request from the bridge folder and writes one
#  reply. See docs/live.md in rpgvxace-mcp for the protocol.
#==============================================================================

module MCPBridge
  DIR = "__BRIDGE_DIR__"
  CONFIG = DIR + "/" + "__CONFIG_FILE__"
  READY = DIR + "/" + "__READY_FILE__"
  REQUEST = DIR + "/" + "__REQUEST_FILE__"
  RESPONSE = DIR + "/" + "__RESPONSE_FILE__"
  CRASH = DIR + "/" + "__CRASH_FILE__"
  KEYS = [__KEYS__]
  NUMERIC_KEYS = {2 => :DOWN, 4 => :LEFT, 6 => :RIGHT, 8 => :UP}
  SECTIONS = [__SECTIONS__]
  CRASH_LOOP_OPS = [__CRASH_LOOP_OPS__]
  REPEAT_DELAY = 24
  REPEAT_INTERVAL = 6
  MAX_ENTRIES = __MAX_ENTRIES__
  MAX_TEXT = __MAX_TEXT__
  TELEPORT_FRAMES = __TELEPORT_FRAMES__
  MAX_PASS_CELLS = __MAX_PASS_CELLS__
  PASS_BITS = [[2, 1], [4, 2], [6, 4], [8, 8]]

  @config = {}
  @frame = 0
  @paused = false
  @announced = false
  @crash = nil
  @step_left = 0
  @pending = nil
  @queue = []
  @gap = false
  @held = {}

  #--------------------------------------------------------------------------
  # * Boot: read what this session asked for
  #--------------------------------------------------------------------------
  def self.boot
    if File.exist?(CONFIG)
      @config = File.open(CONFIG, "rb") { |f| Marshal.load(f) }
    end
    @paused = @config["paused"] ? true : false
  rescue RGSSReset, SystemExit
    raise
  rescue Exception => e
    @config = {"config_error" => e.message}
  end

  def self.skip_title?
    @config["skip_title"] ? true : false
  end

  #--------------------------------------------------------------------------
  # * Files
  #--------------------------------------------------------------------------
  def self.write(path, value)
    temporary = path + ".tmp"
    File.open(temporary, "wb") { |f| f.write(Marshal.dump(value)) }
    File.delete(path) if File.exist?(path)
    File.rename(temporary, path)
  end

  def self.text(value)
    string = value.to_s.dup
    string.force_encoding("UTF-8")
    string[0, MAX_TEXT]
  end

  def self.scene_name
    SceneManager.scene ? SceneManager.scene.class.name : nil
  end

  def self.status
    {"frame" => @frame, "paused" => @paused, "scene" => scene_name,
     "crashed" => !@crash.nil?}
  end

  def self.respond(seq, ok, result, error = nil)
    reply = status
    reply["seq"] = seq
    reply["ok"] = ok
    if ok
      reply["result"] = result
    else
      reply["error"] = text(error)
    end
    write(RESPONSE, reply)
  end

  #--------------------------------------------------------------------------
  # * Once a frame, from Graphics.update
  #--------------------------------------------------------------------------
  def self.frame_tick
    @frame += 1
    announce unless @announced
    if @step_left > 0
      @step_left -= 1
      finish_step if @step_left == 0
    end
    check_teleport if @pending && @pending[1] == "teleport"
    poll
    while @paused
      Graphics.mcp_bridge_update
      poll
    end
  end

  def self.announce
    @announced = true
    write(READY, {"token" => @config["token"], "pid" => Process.pid,
                  "frame" => @frame, "scene" => scene_name})
  end

  def self.poll
    return if @pending
    return unless File.exist?(REQUEST)
    raw = File.open(REQUEST, "rb") { |f| f.read }
    File.delete(REQUEST)
    request = Marshal.load(raw)
    raise TypeError, "a request is a Hash" unless request.is_a?(Hash)
    handle(request)
  rescue RGSSReset, SystemExit
    raise
  rescue Exception => e
    respond(request.is_a?(Hash) ? request["seq"] : nil, false, nil,
            "#{e.class}: #{e.message}")
  end

  def self.handle(request)
    seq = request["seq"]
    op = request["op"]
    args = request["args"] || {}
    if @crash && !CRASH_LOOP_OPS.include?(op)
      respond(seq, false, nil, "the game raised #{@crash["class"]} " +
              "(#{@crash["message"]}); only #{CRASH_LOOP_OPS.join(", ")} " +
              "answer now")
      return
    end
    case op
    when "ping"
      respond(seq, true, status)
    when "state"
      respond(seq, true, state(args))
    when "pause"
      @paused = true
      respond(seq, true, status)
    when "resume"
      @paused = false
      respond(seq, true, status)
    when "step"
      frames = args["frames"].to_i
      raise ArgumentError, "frames must be at least 1" if frames < 1
      @step_left = frames
      @pending = [seq, "step"]
      @paused = false
    when "input"
      respond(seq, true, enqueue(args))
    when "set"
      respond(seq, true, set(args))
    when "teleport"
      teleport(seq, args)
    when "eval"
      respond(seq, true, evaluate(args))
    when "passability"
      respond(seq, true, passability(args))
    when "locate"
      respond(seq, true, locate(args))
    when "face"
      respond(seq, true, face(args))
    when "quit"
      respond(seq, true, status)
      exit
    else
      respond(seq, false, nil, "unknown op #{op}")
    end
  end

  def self.finish_step
    seq = @pending ? @pending[0] : nil
    @pending = nil
    @paused = true
    respond(seq, true, status)
  end

  #--------------------------------------------------------------------------
  # * A Ruby exception the game did not handle
  #--------------------------------------------------------------------------
  def self.crashed(error)
    @crash = {"class" => error.class.name, "message" => text(error.message),
              "backtrace" => backtrace(error), "frame" => @frame,
              "scene" => scene_name}
    write(CRASH, @crash)
    if @pending
      respond(@pending[0], false, nil,
              "the game raised #{@crash["class"]}: #{@crash["message"]}")
      @pending = nil
    end
    @paused = false
    @step_left = 0
    loop do
      Graphics.mcp_bridge_update
      poll
    end
  end

  def self.backtrace(error)
    (error.backtrace || [])[0, 40].map do |line|
      text(line).gsub(/\{(\d+)\}/) do
        slot = $RGSS_SCRIPTS[$1.to_i]
        "{#{$1}} #{slot ? text(slot[1]) : "?"}"
      end
    end
  end

  #--------------------------------------------------------------------------
  # * Virtual input
  #--------------------------------------------------------------------------
  def self.enqueue(args)
    keys = (args["keys"] || []).map { |key| key.to_s.to_sym }
    unknown = keys - KEYS
    raise ArgumentError, "unknown keys #{unknown.inspect}" unless unknown.empty?
    @queue.push([keys, [args["frames"].to_i, 1].max])
    result = status
    result["queued"] = @queue.size
    result
  end

  def self.input_tick
    pressed = []
    if @gap
      @gap = false
    elsif !@queue.empty?
      entry = @queue.first
      pressed = entry[0]
      entry[1] -= 1
      if entry[1] <= 0
        @queue.shift
        @gap = true
      end
    end
    held = {}
    pressed.each { |key| held[key] = (@held[key] || 0) + 1 }
    @held = held
  end

  def self.key_of(key)
    NUMERIC_KEYS[key] || key
  end

  def self.press?(key)
    @held.key?(key_of(key))
  end

  def self.trigger?(key)
    @held[key_of(key)] == 1
  end

  def self.repeat?(key)
    count = @held[key_of(key)]
    return false unless count
    return true if count == 1
    count >= REPEAT_DELAY && (count - REPEAT_DELAY) % REPEAT_INTERVAL == 0
  end

  def self.dir4
    return 2 if @held.key?(:DOWN)
    return 4 if @held.key?(:LEFT)
    return 6 if @held.key?(:RIGHT)
    return 8 if @held.key?(:UP)
    0
  end

  def self.dir8
    value = 5
    value -= 3 if @held.key?(:DOWN)
    value += 3 if @held.key?(:UP)
    value -= 1 if @held.key?(:LEFT)
    value += 1 if @held.key?(:RIGHT)
    value == 5 ? 0 : value
  end

  #--------------------------------------------------------------------------
  # * State
  #--------------------------------------------------------------------------
  def self.state(args)
    out = status
    out["crash"] = @crash
    out["truncated"] = []
    out["errors"] = {}
    (args["sections"] || SECTIONS).each do |section|
      begin
        out[section] = send("state_" + section.to_s, out)
      rescue RGSSReset, SystemExit
        raise
      rescue Exception => e
        out[section] = nil
        out["errors"][section] = text("#{e.class}: #{e.message}")
      end
    end
    out
  end

  def self.state_player(out)
    return nil unless $game_player && $game_map && $game_map.map_id > 0
    {"map_id" => $game_map.map_id, "x" => $game_player.x,
     "y" => $game_player.y, "direction" => $game_player.direction,
     "moving" => $game_player.moving?, "transferring" => $game_player.transfer?,
     "map_name" => text($game_map.display_name)}
  end

  def self.state_party(out)
    return nil unless $game_party
    $game_party.members.map do |actor|
      {"id" => actor.id, "name" => text(actor.name), "level" => actor.level,
       "hp" => actor.hp, "mhp" => actor.mhp, "mp" => actor.mp,
       "mmp" => actor.mmp, "tp" => actor.tp.to_i,
       "states" => actor.states.map { |s| s.id }}
    end
  end

  def self.state_gold(out)
    $game_party ? $game_party.gold : nil
  end

  def self.state_items(out)
    return nil unless $game_party
    found = {}
    ["items", "weapons", "armors"].each do |kind|
      counts = $game_party.instance_variable_get("@" + kind) || {}
      found[kind] = counts.reject { |id, count| count == 0 }
    end
    found
  end

  def self.capped(out, section, pairs)
    found = {}
    pairs.each do |key, value|
      if found.size >= MAX_ENTRIES
        out["truncated"].push(section)
        break
      end
      found[key] = value
    end
    found
  end

  def self.state_switches(out)
    return nil unless $game_switches
    ids = (1...$data_system.switches.size).select { |id| $game_switches[id] }
    capped(out, "switches", ids.map { |id| [id, true] })
  end

  def self.state_variables(out)
    return nil unless $game_variables
    pairs = []
    (1...$data_system.variables.size).each do |id|
      value = $game_variables[id]
      next if value == 0 || value.nil?
      pairs.push([id, value.is_a?(Numeric) ? value : text(value.inspect)])
    end
    capped(out, "variables", pairs)
  end

  def self.state_self_switches(out)
    return nil unless $game_self_switches
    data = $game_self_switches.instance_variable_get(:@data) || {}
    keys = data.keys.select { |key| data[key] }
    listed = capped(out, "self_switches", keys.map { |key| [key, true] })
    listed.keys.map { |key| key.map { |part| part.is_a?(String) ? text(part) : part } }
  end

  def self.state_interpreter(out)
    return nil unless $game_map
    interpreter = $game_map.interpreter
    list = interpreter.instance_variable_get(:@list)
    index = interpreter.instance_variable_get(:@index)
    command = list && index ? list[index] : nil
    {"running" => interpreter.running?, "event_id" => interpreter.event_id,
     "map_id" => interpreter.instance_variable_get(:@map_id),
     "index" => index, "code" => command ? command.code : nil,
     "in_battle" => $game_party ? $game_party.in_battle : false}
  end

  def self.state_message(out)
    return nil unless $game_message
    {"busy" => $game_message.busy?, "visible" => $game_message.visible,
     "texts" => $game_message.texts.map { |line| text(line) },
     "choices" => $game_message.choices.map { |line| text(line) }}
  end

  #--------------------------------------------------------------------------
  # * Changing the game, for test setup
  #--------------------------------------------------------------------------
  def self.check_range(what, id, size)
    return if id.is_a?(Integer) && id >= 1 && id < size
    raise ArgumentError, "#{what} #{id} is outside 1..#{size - 1}"
  end

  def self.item_table(kind)
    {"item" => $data_items, "weapon" => $data_weapons,
     "armor" => $data_armors}[kind]
  end

  def self.set(args)
    switches = args["switches"] || {}
    variables = args["variables"] || {}
    self_switches = args["self_switches"] || []
    items = args["items"] || []
    switches.each_key { |id| check_range("switch", id, $data_system.switches.size) }
    variables.each_key { |id| check_range("variable", id, $data_system.variables.size) }
    items.each do |kind, id, count|
      table = item_table(kind)
      raise ArgumentError, "unknown item kind #{kind}" unless table
      check_range(kind, id, table.size)
      raise ArgumentError, "#{kind} #{id} is empty" unless table[id]
    end
    switches.each { |id, value| $game_switches[id] = value }
    variables.each { |id, value| $game_variables[id] = value }
    self_switches.each do |map_id, event_id, letter, value|
      $game_self_switches[[map_id, event_id, letter]] = value
    end
    $game_party.gain_gold(args["gold"] - $game_party.gold) if args["gold"]
    items.each do |kind, id, count|
      item = item_table(kind)[id]
      $game_party.gain_item(item, count - $game_party.item_number(item))
    end
    $game_map.need_refresh = true if $game_map
    status
  end

  def self.teleport(seq, args)
    unless SceneManager.scene.is_a?(Scene_Map)
      raise ArgumentError, "teleport needs the map scene; the game is in " +
                           "#{scene_name}"
    end
    map_id = args["map_id"]
    unless $data_mapinfos[map_id]
      raise ArgumentError, "map #{map_id} is not in this game's map list"
    end
    map = load_data(sprintf("Data/Map%03d.rvdata2", map_id))
    x = args["x"]
    y = args["y"]
    unless x.between?(0, map.width - 1) && y.between?(0, map.height - 1)
      raise ArgumentError, "(#{x}, #{y}) is outside map #{map_id}, which " +
                           "is #{map.width}x#{map.height}"
    end
    direction = args["direction"] || $game_player.direction
    $game_temp.fade_type = 2 if $game_temp.respond_to?(:fade_type=)
    $game_player.reserve_transfer(map_id, x, y, direction)
    @pending = [seq, "teleport", @frame + TELEPORT_FRAMES, @paused]
    @paused = false
  end

  def self.check_teleport
    seq = @pending[0]
    if !$game_player.transfer?
      @paused = @pending[3]
      @pending = nil
      result = status
      result["player"] = state_player(result)
      respond(seq, true, result)
    elsif @frame >= @pending[2]
      @paused = @pending[3]
      @pending = nil
      respond(seq, false, nil, "the transfer was reserved but did not " +
              "happen within #{TELEPORT_FRAMES} frames; a message or a " +
              "running event may be holding the map (see state)")
    end
  end

  #--------------------------------------------------------------------------
  # * Walking: what the game itself says the player can do
  #--------------------------------------------------------------------------
  def self.clip(value, size)
    [[value.to_i, 0].max, size - 1].min
  end

  # One hex digit per cell: bit 1 is down, 2 left, 4 right, 8 up, set when
  # the player could step that way from the cell. Asked of the game's own
  # passable?, so events that block and the vehicle rules are included.
  def self.passability(args)
    unless $game_player && $game_map && $game_map.map_id > 0
      raise ArgumentError, "there is no map to read"
    end
    width = $game_map.width
    height = $game_map.height
    x0 = clip(args["x0"] || 0, width)
    y0 = clip(args["y0"] || 0, height)
    x1 = clip(args["x1"] || width - 1, width)
    y1 = clip(args["y1"] || height - 1, height)
    if x1 < x0 || y1 < y0
      raise ArgumentError, "the window is empty (#{x0},#{y0} to #{x1},#{y1})"
    end
    cells = (x1 - x0 + 1) * (y1 - y0 + 1)
    if cells > MAX_PASS_CELLS
      raise ArgumentError, "a window of #{cells} cells is more than the " +
                           "#{MAX_PASS_CELLS} one read allows"
    end
    rows = []
    (y0..y1).each do |y|
      line = ""
      (x0..x1).each do |x|
        line << pass_bits(x, y).to_s(16)
      end
      rows.push(line)
    end
    {"map_id" => $game_map.map_id, "width" => width, "height" => height,
     "x0" => x0, "y0" => y0, "x1" => x1, "y1" => y1, "rows" => rows}
  end

  def self.pass_bits(x, y)
    bits = 0
    PASS_BITS.each do |direction, bit|
      begin
        bits |= bit if $game_player.passable?(x, y, direction)
      rescue StandardError
        bits |= 0
      end
    end
    bits
  end

  def self.locate(args)
    id = args["event_id"]
    unless id.is_a?(Integer) && id >= 1
      raise ArgumentError, "event_id must be a whole number from 1"
    end
    event = $game_map.events[id]
    raise ArgumentError, "map #{$game_map.map_id} has no event #{id}" unless event
    {"map_id" => $game_map.map_id, "event_id" => id, "x" => event.x,
     "y" => event.y, "direction" => event.direction,
     "through" => event.through ? true : false,
     "erased" => event.instance_variable_get(:@erased) ? true : false}
  end

  def self.face(args)
    direction = args["direction"]
    unless NUMERIC_KEYS.key?(direction)
      raise ArgumentError, "direction must be 2, 4, 6 or 8"
    end
    if $game_player.moving?
      raise ArgumentError, "the player is moving; step until it stops"
    end
    $game_player.set_direction(direction)
    result = status
    result["direction"] = $game_player.direction
    result
  end

  #--------------------------------------------------------------------------
  # * The escape hatch, if this session allowed it
  #--------------------------------------------------------------------------
  def self.evaluate(args)
    unless @config["allow_eval"]
      raise ArgumentError, "eval was not enabled when this session " +
                           "started; start it with allow_eval"
    end
    value = eval(args["code"].to_s, TOPLEVEL_BINDING, "mcp_eval")
    {"inspect" => text(safe_inspect(value)), "class" => value.class.name}
  end

  def self.safe_inspect(value)
    value.inspect
  rescue RGSSReset, SystemExit
    raise
  rescue Exception
    "#<#{value.class} (inspect raised)>"
  end
end

#==============================================================================
# ** Hooks
#==============================================================================

class << Graphics
  alias_method :mcp_bridge_update, :update

  def update
    mcp_bridge_update
    MCPBridge.frame_tick
  end

  def wait(duration)
    duration.times do
      update
    end
  end

  def fadeout(duration)
    start = brightness
    duration.times do |i|
      self.brightness = start - start * (i + 1) / duration
      update
    end
    self.brightness = 0
  end

  def fadein(duration)
    start = brightness
    duration.times do |i|
      self.brightness = start + (255 - start) * (i + 1) / duration
      update
    end
    self.brightness = 255
  end
end

class << Input
  alias_method :mcp_bridge_update, :update
  alias_method :mcp_bridge_press?, :press?
  alias_method :mcp_bridge_trigger?, :trigger?
  alias_method :mcp_bridge_repeat?, :repeat?
  alias_method :mcp_bridge_dir4, :dir4
  alias_method :mcp_bridge_dir8, :dir8

  def update
    mcp_bridge_update
    MCPBridge.input_tick
  end

  def press?(key)
    MCPBridge.press?(key) || mcp_bridge_press?(key)
  end

  def trigger?(key)
    MCPBridge.trigger?(key) || mcp_bridge_trigger?(key)
  end

  def repeat?(key)
    MCPBridge.repeat?(key) || mcp_bridge_repeat?(key)
  end

  def dir4
    value = MCPBridge.dir4
    value == 0 ? mcp_bridge_dir4 : value
  end

  def dir8
    value = MCPBridge.dir8
    value == 0 ? mcp_bridge_dir8 : value
  end
end

class << SceneManager
  alias_method :mcp_bridge_run, :run
  alias_method :mcp_bridge_first_scene_class, :first_scene_class

  def run
    mcp_bridge_run
  rescue RGSSReset, SystemExit
    raise
  rescue Exception => e
    MCPBridge.crashed(e)
  end

  def first_scene_class
    return mcp_bridge_first_scene_class if $BTEST || !MCPBridge.skip_title?
    DataManager.setup_new_game
    $game_map.autoplay
    Scene_Map
  end
end

MCPBridge.boot
'''


def _symbols(names: "tuple[str, ...]") -> str:
    return ", ".join(":" + name for name in names)


def _strings(names: "tuple[str, ...]") -> str:
    return ", ".join('"%s"' % name for name in names)


def ruby_source() -> str:
    """The bridge script, with this side's names and limits filled in."""
    filled = {
        "__BRIDGE_DIR__": wire.BRIDGE_DIR,
        "__CONFIG_FILE__": wire.CONFIG_FILE,
        "__READY_FILE__": wire.READY_FILE,
        "__REQUEST_FILE__": wire.REQUEST_FILE,
        "__RESPONSE_FILE__": wire.RESPONSE_FILE,
        "__CRASH_FILE__": wire.CRASH_FILE,
        "__KEYS__": _symbols(KEYS),
        "__SECTIONS__": _strings(STATE_SECTIONS),
        "__CRASH_LOOP_OPS__": _strings(CRASH_LOOP_OPS),
        "__MAX_ENTRIES__": str(MAX_STATE_ENTRIES),
        "__MAX_TEXT__": str(MAX_BRIDGE_TEXT),
        "__TELEPORT_FRAMES__": str(TELEPORT_FRAMES),
        "__MAX_PASS_CELLS__": str(MAX_PASS_CELLS),
    }
    text = _RUBY
    for placeholder, value in filled.items():
        text = text.replace(placeholder, value)
    return text


_SLOT_INFLATE_LIMIT = 32 * 1024 * 1024


def _slot_code(row: Any) -> bytes:
    """One slot's Ruby, or nothing if the slot is not shaped like one."""
    if not (isinstance(row, list) and len(row) >= 3
            and isinstance(row[2], RubyString)):
        return b""
    # Bounded: a slot is a few hundred KB of Ruby at most, and a crafted
    # Scripts file can hold a slot that inflates to a gigabyte.
    stream = zlib.decompressobj()
    try:
        code = stream.decompress(row[2].data, _SLOT_INFLATE_LIMIT)
    except zlib.error:
        return b""
    if stream.unconsumed_tail:
        return b""
    return code


def _slot_name(row: Any) -> bytes:
    if isinstance(row, list) and len(row) >= 2 and isinstance(row[1], RubyString):
        return row[1].data
    return b""


def inject(scripts: list) -> int:
    """Insert the bridge directly above Main in a script list; return where.

    Main is the last slot that calls `rgss_main`. A project with none never
    starts, and one that already carries the bridge is a copy that was
    injected before -- both are refused rather than guessed at.
    """
    if not isinstance(scripts, list):
        raise ValueError("the Scripts file does not hold a list of slots")
    if any(_slot_name(row) == BRIDGE_SLOT_NAME.encode() for row in scripts):
        raise ValueError("this script list already carries the live bridge")
    mains = [index for index, row in enumerate(scripts)
             if b"rgss_main" in _slot_code(row)]
    if not mains:
        raise ValueError(
            "no script slot calls rgss_main, so there is no Main to put the "
            "bridge above and the game would not start anyway")
    index = mains[-1]
    scripts.insert(index, [
        0x6D6370,
        RubyString(BRIDGE_SLOT_NAME.encode("utf-8"), {"E": True}),
        RubyString(zlib.compress(ruby_source().encode("ascii"))),
    ])
    return index
