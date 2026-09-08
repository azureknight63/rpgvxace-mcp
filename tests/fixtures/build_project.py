"""Build a small, synthetic RPG Maker VX Ace (or VX) project.

The data classes carry the instance variables a real project uses -- the Ace
field sets were taken from the RGSS3 reference and cross-checked against a real
editor-created project -- but this is still an approximation assembled by hand.
It exists so the tools can be exercised end-to-end, and so anyone can try the
server without owning RPG Maker.

Run it directly to create a project you can point the server at:

    python tests/fixtures/build_project.py /tmp/demo            # VX Ace
    python tests/fixtures/build_project.py /tmp/demo-vx vx      # VX
"""

from __future__ import annotations

import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.rubytypes import RubyHash, RubyObject, RubyString, Table, Tone  # noqa: E402
from rpgvxace_mcp.schema import (  # noqa: E402
    ACE_PROFILE,
    VX_PROFILE,
    new_event,
    new_event_command,
    new_map_info,
    new_page,
)

ENCODING = "utf-8"


class Fixture:
    """Builds one project. Everything engine-specific goes through here."""

    def __init__(self, engine: str = "vx_ace"):
        self.profile = ACE_PROFILE if engine == "vx_ace" else VX_PROFILE
        self.ace = engine == "vx_ace"

    # -- primitives -----------------------------------------------------------
    def S(self, text: str = "") -> RubyString:
        """A string as this engine's Ruby writes it.

        RGSS3 runs Ruby 1.9, which wraps every string in an ivar record carrying
        its encoding; RGSS2 runs Ruby 1.8 and writes bare bytes.
        """
        data = text.encode(ENCODING)
        return RubyString(data, {"E": True} if self.ace else None)

    def audio(self, kind: str = "bgm", name: str = "", volume: int = 100,
              pitch: int = 100) -> RubyObject:
        obj = self.profile.new_audio_file(kind, "", volume, pitch)
        obj.set("name", self.S(name))
        return obj

    def obj(self, cls: str, **fields) -> RubyObject:
        out = RubyObject(cls)
        for key, value in fields.items():
            out.set(key.rstrip("_"), value)
        return out

    def feature(self, code: int, data_id: int, value: float) -> RubyObject:
        return self.obj("RPG::BaseItem::Feature", code=code, data_id=data_id,
                        value=value)

    # -- database -------------------------------------------------------------
    def actor(self, actor_id: int, name: str, class_id: int, character: str,
              face: str) -> RubyObject:
        if self.ace:
            return self.obj(
                "RPG::Actor", id=actor_id, name=self.S(name),
                nickname=self.S(""), class_id=class_id, initial_level=1,
                max_level=99, character_name=self.S(character), character_index=0,
                face_name=self.S(face), face_index=0, equips=[1, 0, 2, 0, 0],
                description=self.S(""), note=self.S(""), icon_index=0,
                features=[self.feature(23, 0, 0.95)],
            )
        parameters = Table(6, 100, 1)
        for level in range(100):
            for stat, base, step in ((0, 400, 50), (1, 80, 10), (2, 15, 2),
                                     (3, 15, 2), (4, 15, 2), (5, 15, 2)):
                parameters.set(stat, level, 0, base + level * step)
        return self.obj(
            "RPG::Actor", id=actor_id, name=self.S(name), class_id=class_id,
            initial_level=1, exp_basis=25, exp_inflation=35,
            character_name=self.S(character), character_index=0,
            face_name=self.S(face), face_index=0, parameters=parameters,
            weapon_id=1, armor1_id=0, armor2_id=0, armor3_id=0, armor4_id=0,
            two_swords_style=False, fix_equipment=False, auto_battle=False,
            super_guard=False, pharmacology=False, critical_bonus=False,
        )

    def character_class(self, class_id: int, name: str,
                        skill_levels: list[tuple[int, int]]) -> RubyObject:
        learnings = [
            self.obj("RPG::Class::Learning", level=level, skill_id=skill_id,
                     note=self.S("")) if self.ace else
            self.obj("RPG::Class::Learning", level=level, skill_id=skill_id)
            for level, skill_id in skill_levels
        ]
        if self.ace:
            params = Table(8, 100, 1)
            for level in range(100):
                for stat, base, step in ((0, 400, 50), (1, 80, 10), (2, 15, 2),
                                         (3, 15, 2), (4, 15, 2), (5, 15, 2),
                                         (6, 15, 2), (7, 15, 2)):
                    params.set(stat, level, 0, base + level * step)
            return self.obj("RPG::Class", id=class_id, name=self.S(name),
                            icon_index=0, description=self.S(""), note=self.S(""),
                            exp_params=[30, 20, 30, 30], params=params,
                            learnings=learnings, features=[])
        return self.obj("RPG::Class", id=class_id, name=self.S(name), position=0,
                        weapon_set=[1], armor_set=[1], element_ranks=[],
                        state_ranks=[], learnings=learnings)

    def damage(self, formula: str, element_id: int = 0) -> RubyObject:
        return self.obj("RPG::UsableItem::Damage", type=1, element_id=element_id,
                        formula=self.S(formula), variance=20, critical=True)

    def effect(self, code: int, data_id: int, value1: float,
               value2: float = 0) -> RubyObject:
        return self.obj("RPG::UsableItem::Effect", code=code, data_id=data_id,
                        value1=value1, value2=value2)

    def item(self, item_id: int, name: str, price: int, description: str,
             recover: int) -> RubyObject:
        if self.ace:
            return self.obj(
                "RPG::Item", id=item_id, name=self.S(name), icon_index=176,
                description=self.S(description), note=self.S(""),
                itype_id=1, price=price, consumable=True, scope=7, occasion=0,
                speed=0, success_rate=100, repeats=1, tp_gain=0, hit_type=0,
                animation_id=0, damage=self.obj(
                    "RPG::UsableItem::Damage", type=0, element_id=0,
                    formula=self.S("0"), variance=20, critical=False),
                effects=[self.effect(11, 0, 0.0, recover)], features=[],
            )
        return self.obj(
            "RPG::Item", id=item_id, name=self.S(name), icon_index=144 + item_id,
            description=self.S(description), scope=7, occasion=0, speed=0,
            animation_id=0, common_event_id=0, price=price, consumable=True,
            recover_hp_rate=0, recover_hp=recover, recover_mp_rate=0, recover_mp=0,
            parameter_type=0, parameter_points=0, plus_state_set=[],
            minus_state_set=[],
        )

    def skill(self, skill_id: int, name: str, mp_cost: int) -> RubyObject:
        if self.ace:
            return self.obj(
                "RPG::Skill", id=skill_id, name=self.S(name), icon_index=112,
                description=self.S(""), note=self.S(""), stype_id=1,
                mp_cost=mp_cost, tp_cost=0, scope=1, occasion=1, speed=0,
                success_rate=100, repeats=1, tp_gain=0, hit_type=1,
                animation_id=1, required_wtype_id1=0, required_wtype_id2=0,
                message1=self.S(" attacks!"), message2=self.S(""),
                damage=self.damage("a.atk * 4 - b.def * 2"), effects=[],
                features=[],
            )
        return self.obj("RPG::Skill", id=skill_id, name=self.S(name),
                        icon_index=100 + skill_id, description=self.S(""),
                        scope=1, occasion=1, mp_cost=mp_cost,
                        base_damage=20 * skill_id, variance=20, animation_id=1,
                        plus_state_set=[], minus_state_set=[])

    def weapon(self, weapon_id: int, name: str, atk: int) -> RubyObject:
        if self.ace:
            params = [0, 0, atk, 0, 0, 0, 0, 0]
            return self.obj("RPG::Weapon", id=weapon_id, name=self.S(name),
                            icon_index=96, description=self.S(""), note=self.S(""),
                            wtype_id=1, etype_id=0, price=100 * weapon_id,
                            animation_id=1, params=params, features=[])
        return self.obj("RPG::Weapon", id=weapon_id, name=self.S(name),
                        icon_index=16 + weapon_id, description=self.S(""),
                        price=100 * weapon_id, animation_id=1, atk=atk, def_=0,
                        spi=0, agi=0, two_handed=False, fast_attack=False,
                        dual_attack=False, critical_bonus=False)

    def armor(self, armor_id: int, name: str, defence: int) -> RubyObject:
        if self.ace:
            params = [0, 0, 0, defence, 0, 0, 0, 0]
            return self.obj("RPG::Armor", id=armor_id, name=self.S(name),
                            icon_index=128, description=self.S(""),
                            note=self.S(""), atype_id=1, etype_id=1,
                            price=80 * armor_id, params=params, features=[])
        return self.obj("RPG::Armor", id=armor_id, name=self.S(name),
                        icon_index=32 + armor_id, description=self.S(""),
                        price=80 * armor_id, kind=0, atk=0, spi=0, agi=0,
                        prevent_critical=False, half_mp_cost=False,
                        double_exp_gain=False)

    def enemy(self, enemy_id: int, name: str, hp: int, exp: int) -> RubyObject:
        action = self.obj("RPG::Enemy::Action", skill_id=1, condition_type=0,
                          condition_param1=0, condition_param2=0, rating=5)
        if self.ace:
            return self.obj(
                "RPG::Enemy", id=enemy_id, name=self.S(name),
                battler_name=self.S("Slime"), battler_hue=0, note=self.S(""),
                description=self.S(""), icon_index=0,
                params=[hp, 10, 12, 10, 10, 10, 10, 10], exp=exp, gold=exp * 2,
                hit=95, eva=5, actions=[action], drop_items=[
                    self.obj("RPG::Enemy::DropItem", kind=1, data_id=1,
                             denominator=4)],
                features=[],
            )
        return self.obj("RPG::Enemy", id=enemy_id, name=self.S(name),
                        battler_name=self.S("Slime"), battler_hue=0, maxhp=hp,
                        maxmp=10, atk=12, spi=10, agi=10, exp=exp, gold=exp * 2,
                        drop_item1_kind=0, drop_item1_id=0, actions=[action],
                        element_ranks=[], state_ranks=[])

    def troop(self, troop_id: int, name: str, enemy_ids: list[int]) -> RubyObject:
        members = [
            self.obj("RPG::Troop::Member", enemy_id=enemy_id, x=200 + i * 80,
                     y=200, hidden=False)
            if self.ace else
            self.obj("RPG::Troop::Member", enemy_id=enemy_id, x=200 + i * 80,
                     y=200, hidden=False, immortal=False)
            for i, enemy_id in enumerate(enemy_ids)
        ]
        return self.obj("RPG::Troop", id=troop_id, name=self.S(name),
                        members=members, pages=[])

    def state(self, state_id: int, name: str) -> RubyObject:
        if self.ace:
            return self.obj(
                "RPG::State", id=state_id, name=self.S(name), icon_index=state_id,
                description=self.S(""), note=self.S(""), restriction=0,
                priority=50, remove_at_battle_end=False,
                remove_by_restriction=False, auto_removal_timing=0, min_turns=1,
                max_turns=1, remove_by_damage=False, chance_by_damage=100,
                remove_by_walking=False, steps_to_remove=100,
                message1=self.S(""), message2=self.S(""), message3=self.S(""),
                message4=self.S(""), features=[],
            )
        return self.obj("RPG::State", id=state_id, name=self.S(name),
                        icon_index=state_id, restriction=0, priority=5,
                        auto_release_prob=0, hold_turn=0, reduce_hit_ratio=0,
                        offset_by_opposite=False, slip_damage=state_id == 2,
                        reduce_hp_by_walking=0)

    def animation(self, animation_id: int, name: str) -> RubyObject:
        frame = self.obj("RPG::Animation::Frame", cell_max=1,
                         cell_data=Table(1, 8, 1,
                                         data=[0, 0, 0, 100, 0, 255, 0, 0]))
        return self.obj("RPG::Animation", id=animation_id, name=self.S(name),
                        animation1_name=self.S("Slash1"), animation1_hue=0,
                        animation2_name=self.S(""), animation2_hue=0, position=1,
                        frame_max=1, frames=[frame], timings=[])

    def tileset(self, tileset_id: int, name: str, sheets: list[str]) -> RubyObject:
        flags = Table(8192, 1, 1, dim=1)
        # Anything on the B-E sheets defaults to blocking, as a fresh tileset does.
        for tile_id in range(1024):
            flags.set(tile_id, 0, 0, 15)
        names = [self.S(sheets[i] if i < len(sheets) else "") for i in range(9)]
        return self.obj("RPG::Tileset", id=tileset_id, mode=0, name=self.S(name),
                        tileset_names=names, note=self.S(""), flags=flags)

    def common_event(self, event_id: int, name: str,
                     commands: list[RubyObject]) -> RubyObject:
        return self.obj("RPG::CommonEvent", id=event_id, name=self.S(name),
                        trigger=0, switch_id=1,
                        list=commands + [new_event_command(0, 0, [])])

    def system(self) -> RubyObject:
        if self.ace:
            terms = self.obj(
                "RPG::System::Terms",
                basic=[self.S(t) for t in ("Level", "Lv", "HP", "HP", "MP", "MP",
                                           "TP", "TP", "EXP", "EXP")],
                params=[self.S(t) for t in ("Max HP", "Max MP", "Attack",
                                            "Defense", "M.Attack", "M.Defense",
                                            "Agility", "Luck", "Hit", "Evasion")],
                etypes=[self.S(t) for t in ("Weapon", "Shield", "Head", "Body",
                                            "Accessory")],
                commands=[self.S(t) for t in ("Fight", "Escape", "Attack",
                                              "Guard", "Item", "Skill", "Equip",
                                              "Status", "Formation", "Save",
                                              "Game End", "Weapon", "Armor",
                                              "Key Item", "Equip", "Optimize",
                                              "Clear", "New Game", "Continue",
                                              "Shutdown", "To Title", "Cancel")],
            )
            vehicle = self.obj("RPG::System::Vehicle", character_name=self.S(""),
                               character_index=0, bgm=self.audio("bgm"),
                               start_map_id=0, start_x=0, start_y=0)
            return self.obj(
                "RPG::System", game_title=self.S("Demo Quest"), version_id=12345,
                japanese=False, party_members=[1, 2],
                currency_unit=self.S("G"),
                elements=[self.S(t) for t in ("", "Physical", "Fire", "Ice")],
                skill_types=[self.S(t) for t in ("", "Special", "Magic")],
                weapon_types=[self.S(t) for t in ("", "Sword", "Staff")],
                armor_types=[self.S(t) for t in ("", "Shield", "Cloth")],
                switches=[None if i == 0 else self.S(n) for i, n in enumerate(
                    ("", "Door Opened", "Met Elder", "Quest Done"))],
                variables=[None if i == 0 else self.S(n) for i, n in enumerate(
                    ("", "Gold Bonus", "Herbs Given"))],
                boat=vehicle, ship=vehicle, airship=vehicle,
                title1_name=self.S("Title"), title2_name=self.S(""),
                opt_draw_title=True, opt_use_midi=False, opt_transparent=False,
                opt_followers=True, opt_slip_death=False, opt_floor_death=False,
                opt_display_tp=True, opt_extra_exp=False,
                window_tone=Tone([0.0, 0.0, 0.0, 0.0]),
                title_bgm=self.audio("bgm", "Theme1"),
                battle_bgm=self.audio("bgm", "Battle1"),
                battle_end_me=self.audio("me", "Victory1"),
                gameover_me=self.audio("me", "Gameover1"), sounds=[],
                test_battlers=[], test_troop_id=1, start_map_id=1, start_x=5,
                start_y=6, battleback1_name=self.S(""),
                battleback2_name=self.S(""), battler_name=self.S("BattleStart"),
                battler_hue=0, edit_map_id=1, terms=terms,
            )
        terms = self.obj(
            "RPG::System::Terms", level=self.S("Level"), level_a=self.S("Lv"),
            hp=self.S("HP"), hp_a=self.S("HP"), mp=self.S("MP"), mp_a=self.S("MP"),
            atk=self.S("Attack"), def_=self.S("Defence"), spi=self.S("Spirit"),
            agi=self.S("Agility"), weapon=self.S("Weapon"),
            armor1=self.S("Shield"), armor2=self.S("Helmet"),
            armor3=self.S("Body Armour"), armor4=self.S("Accessory"),
            weapon1=self.S("Weapon"), attack=self.S("Attack"),
            skill=self.S("Skill"), guard=self.S("Guard"), item=self.S("Item"),
            equip=self.S("Equip"), status=self.S("Status"), save=self.S("Save"),
            game_end=self.S("End Game"),
        )
        return self.obj(
            "RPG::System", game_title=self.S("Demo Quest"), version_id=12345,
            party_members=[1, 2],
            elements=[None, self.S("Physical"), self.S("Fire"), self.S("Ice")],
            switches=[None, self.S("Door Opened"), self.S("Met Elder"),
                      self.S("Quest Done")],
            variables=[None, self.S("Gold Bonus"), self.S("Herbs Given")],
            start_map_id=1, start_x=5, start_y=6,
            title_bgm=self.audio("bgm", "Theme1"),
            battle_bgm=self.audio("bgm", "Battle1"),
            battle_end_me=self.audio("me", "Victory1"),
            gameover_me=self.audio("me", "Gameover1"), sounds=[],
            test_battlers=[], test_troop_id=1, title_name=self.S("Title"),
            gameover_name=self.S("GameOver"), window_skin_name=self.S("Window"),
            battler_name=self.S("BattleStart"), battler_hue=0, terms=terms,
            edit_map_id=1,
        )

    # -- maps -----------------------------------------------------------------
    def encounter(self, troop_id: int, weight: int = 5) -> object:
        """VX lists troop ids; Ace wraps each in an RPG::Map::Encounter."""
        if not self.ace:
            return troop_id
        return self.obj("RPG::Map::Encounter", troop_id=troop_id, weight=weight,
                        region_set=[])

    def blank_map(self, width: int, height: int, tileset_id: int = 1) -> RubyObject:
        game_map = self.profile.new_map(width, height, tileset_id)
        # Re-encode template strings through this engine's string form.
        for field in ("parallax_name", "display_name", "note",
                      "battleback1_name", "battleback2_name"):
            if game_map.has(field):
                game_map.set(field, self.S(""))
        game_map.set("bgm", self.audio("bgm"))
        game_map.set("bgs", self.audio("bgs", "", 80))
        return game_map

    def villager_event(self) -> RubyObject:
        """Dialogue, a branch and a self switch -- the shape events usually take."""
        event = new_event(1, "Elder", 5, 4)
        event.set("name", self.S("Elder"))
        page = event.get("pages")[0]
        page.get("graphic").set("character_name", self.S("People1"))
        page.get("graphic").set("character_index", 2)
        page.get("condition").set("self_switch_ch", self.S("A"))
        page.set("list", [
            new_event_command(101, 0, [self.S("Actor1"), 0, 0, 2]),
            new_event_command(401, 0, [self.S("Welcome to Riverbend, traveller.")]),
            new_event_command(401, 0, [self.S("The bridge east has been out for weeks.")]),
            new_event_command(111, 0, [0, 1, 0]),
            new_event_command(101, 1, [self.S("Actor1"), 0, 0, 2]),
            new_event_command(401, 1, [self.S("You opened the gate! Thank you.")]),
            new_event_command(121, 1, [2, 2, 0]),
            new_event_command(412, 0, []),
            new_event_command(0, 0, []),
        ])

        second = new_page()
        second.get("condition").set("self_switch_ch", self.S("A"))
        second.get("condition").set("switch1_valid", True)
        second.get("condition").set("switch1_id", 3)
        second.get("graphic").set("character_name", self.S("People1"))
        second.set("list", [
            new_event_command(101, 0, [self.S(""), 0, 0, 2]),
            new_event_command(401, 0, [self.S("Safe travels.")]),
            new_event_command(0, 0, []),
        ])
        event.set("pages", [page, second])
        return event

    def door_event(self) -> RubyObject:
        event = new_event(2, "Cave Entrance", 8, 2)
        event.set("name", self.S("Cave Entrance"))
        page = event.get("pages")[0]
        page.get("condition").set("self_switch_ch", self.S("A"))
        page.set("trigger", 1)
        page.set("list", [
            new_event_command(250, 0, [self.audio("se", "Move1", 80)]),
            new_event_command(201, 0, [0, 2, 3, 8, 2, 0]),
            new_event_command(0, 0, []),
        ])
        return event

    def chest_event(self) -> RubyObject:
        event = new_event(1, "Chest", 3, 3)
        event.set("name", self.S("Chest"))
        page = event.get("pages")[0]
        page.get("condition").set("self_switch_ch", self.S("A"))
        page.get("graphic").set("character_name", self.S("!Chest"))
        page.set("list", [
            new_event_command(126, 0, [1, 0, 0, 2]),
            new_event_command(101, 0, [self.S(""), 0, 0, 2]),
            new_event_command(401, 0, [self.S("Found 2 Potions!")]),
            new_event_command(123, 0, [self.S("A"), 0]),
            new_event_command(0, 0, []),
        ])
        closed = new_page()
        closed.get("condition").set("self_switch_ch", self.S("A"))
        closed.get("condition").set("self_switch_valid", True)
        closed.get("graphic").set("character_name", self.S("!Chest"))
        closed.get("graphic").set("character_index", 1)
        event.set("pages", [page, closed])
        return event

    def town_map(self) -> RubyObject:
        game_map = self.blank_map(20, 15, tileset_id=1)
        game_map.set("autoplay_bgm", True)
        game_map.set("bgm", self.audio("bgm", "Town1"))
        game_map.set("encounter_step", 30)
        if self.ace:
            game_map.set("display_name", self.S("Riverbend"))
        data = game_map.get("data")
        for y in range(15):
            for x in range(20):
                data.set(x, y, 0, 2816)
        for x in range(20):
            data.set(x, 0, 1, 20)
        events = RubyHash()
        events.set(1, self.villager_event())
        events.set(2, self.door_event())
        game_map.set("events", events)
        return game_map

    def cave_map(self) -> RubyObject:
        game_map = self.blank_map(12, 10, tileset_id=2)
        game_map.set("encounter_list", [self.encounter(1), self.encounter(2)])
        game_map.set("encounter_step", 20)
        data = game_map.get("data")
        for y in range(10):
            for x in range(12):
                data.set(x, y, 0, 2048)
        if self.ace:
            # Region 1 over the back of the cave, where encounters are meant to
            # happen; regions live on tile layer 3.
            for y in range(5, 10):
                for x in range(12):
                    data.set(x, y, 3, 1)
        events = RubyHash()
        events.set(1, self.chest_event())
        game_map.set("events", events)
        return game_map

    def scripts(self) -> list:
        sources = [
            (13, "Game_Temp", "class Game_Temp\n  def initialize\n  end\nend\n"),
            (14, "Game_System", "class Game_System\n  attr_accessor :save_disabled\nend\n"),
            (15, "Main", "rgss_main { SceneManager.run }\n"),
        ]
        return [
            [magic, self.S(name), RubyString(zlib.compress(code.encode(ENCODING)))]
            for magic, name, code in sources
        ]

    # -- assembly -------------------------------------------------------------
    def files(self) -> dict[str, object]:
        map_infos = RubyHash()
        for map_id, (name, parent, order) in enumerate(
                [("Riverbend", 0, 0), ("Riverbend Cave", 1, 1)], start=1):
            info = new_map_info(name, parent, order)
            info.set("name", self.S(name))
            map_infos.set(map_id, info)

        out = {
            "Actors": [None,
                       self.actor(1, "Ralph", 1, "Actor1", "Actor1"),
                       self.actor(2, "Ulrika", 2, "Actor1", "Actor1")],
            "Classes": [None,
                        self.character_class(1, "Fighter", [(3, 1)]),
                        self.character_class(2, "Mage", [(2, 2)])],
            "Skills": [None, self.skill(1, "Power Slash", 4),
                       self.skill(2, "Fire", 6)],
            "Items": [None,
                      self.item(1, "Potion", 50, "Restores 100 HP.", 100),
                      self.item(2, "Hi-Potion", 300, "Restores 500 HP.", 500)],
            "Weapons": [None, self.weapon(1, "Bronze Sword", 14),
                        self.weapon(2, "Oak Staff", 8)],
            "Armors": [None, self.armor(1, "Leather Shield", 6),
                       self.armor(2, "Cloth Robe", 4)],
            "Enemies": [None, self.enemy(1, "Slime", 60, 8),
                        self.enemy(2, "Bat", 45, 6)],
            "Troops": [None, self.troop(1, "Slime", [1]),
                       self.troop(2, "Slime x2", [1, 1])],
            "States": [None, self.state(1, "Knockout"), self.state(2, "Poison")],
            "Animations": [None, self.animation(1, "Hit Physical")],
            "CommonEvents": [None, self.common_event(1, "Heal Party", [
                new_event_command(314, 0, [0, 1])])],
            "System": self.system(),
            "MapInfos": map_infos,
            "Scripts": self.scripts(),
            "Map001": self.town_map(),
            "Map002": self.cave_map(),
        }
        if self.ace:
            out["Tilesets"] = [None,
                               self.tileset(1, "Field", ["World_A1", "World_A2",
                                                         "", "", "", "World_B"]),
                               self.tileset(2, "Dungeon", ["Dungeon_A1",
                                                           "Dungeon_A2", "", "",
                                                           "", "Dungeon_B"])]
        else:
            out["Areas"] = RubyHash()
        return out

    def asset_dirs(self) -> dict[str, list[str]]:
        assets = {
            "Graphics/Characters": ["People1.png", "!Chest.png", "Actor1.png"],
            "Graphics/Faces": ["Actor1.png"],
            "Graphics/Battlers": ["Slime.png"],
            "Graphics/Parallaxes": ["BlueSky.png"],
            # The animation in the database names this, so the fixture has to
            # ship it: a fixture project should be internally consistent, or
            # every asset test has to work around its own gaps.
            "Graphics/Animations": ["Slash1.png"],
            "Audio/BGM": ["Town1.ogg", "Theme1.ogg", "Battle1.ogg"],
            "Audio/SE": ["Move1.ogg"],
            "Audio/ME": ["Victory1.ogg", "Gameover1.ogg"],
        }
        if self.ace:
            assets["Graphics/System"] = ["Window.png", "IconSet.png"]
            assets["Graphics/Tilesets"] = ["World_A1.png", "World_A2.png",
                                           "World_B.png", "Dungeon_A1.png",
                                           "Dungeon_A2.png", "Dungeon_B.png"]
            assets["Graphics/Titles1"] = ["Title.png"]
            assets["Graphics/Battlebacks1"] = ["Grassland.png"]
            assets["Graphics/Battlebacks2"] = ["Grassland.png"]
        else:
            assets["Graphics/System"] = ["Window.png", "TileA1.png", "TileA2.png",
                                         "TileB.png", "Title.png", "GameOver.png"]
        return assets


def build(destination, engine: str = "vx_ace") -> Path:
    fixture = Fixture(engine)
    destination = Path(destination)
    data = destination / "Data"
    data.mkdir(parents=True, exist_ok=True)

    for relative, names in fixture.asset_dirs().items():
        directory = destination / relative
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_bytes(b"")

    (destination / fixture.profile.project_file).write_text(
        "RPGVXAce 1.02\n" if fixture.ace else "RPGVX 1.02\n", encoding="ascii")
    (destination / "Game.ini").write_text(
        "[Game]\nLibrary=%s\nScripts=Data/Scripts%s\nTitle=Demo Quest\n"
        % ("System/RGSS301.dll" if fixture.ace else "RGSS200J.dll",
           fixture.profile.extension),
        encoding="ascii")

    for stem, value in fixture.files().items():
        (data / (stem + fixture.profile.extension)).write_bytes(
            marshal_rb.dump(value))
    return destination


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "demo-project")
    which = sys.argv[2] if len(sys.argv) > 2 else "vx_ace"
    build(target, which)
    profile = ACE_PROFILE if which == "vx_ace" else VX_PROFILE
    print("Wrote a demo %s project to %s" % (profile.label, target.resolve()))
