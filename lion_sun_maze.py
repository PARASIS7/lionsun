"""Lion & Sun Maze - a full-screen, three-map arcade game made with Pygame."""

from __future__ import annotations

import argparse
import math
import random
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Iterable

try:
    import pygame
except ModuleNotFoundError as error:
    if error.name != "pygame":
        raise
    # Directly opening this file may use a global Python installation that has
    # no Pygame.  Transparently relaunch with the project's prepared runtime.
    local_python = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
    if local_python.exists() and Path(sys.executable).resolve() != local_python.resolve():
        completed = subprocess.run(
            [str(local_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            check=False,
        )
        raise SystemExit(completed.returncode)
    raise SystemExit(
        "Pygame is not installed. Run run_game.bat once, or install dependencies with: "
        "python -m pip install -r requirements.txt"
    ) from error


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FPS = 60
WINDOWED_SIZE = (1280, 720)
ASSET_DIR = Path(__file__).resolve().parent / "assets"

BG = (8, 9, 20)
INK = (242, 239, 221)
GOLD = (255, 190, 45)
GOLD_LIGHT = (255, 226, 91)
LEAF = (52, 183, 105)
WALL_DARK = (72, 42, 24)
WALL_BLUE = (20, 99, 154)
WALL_LIGHT = (41, 185, 194)

UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)
STOP = (0, 0)
DIRECTIONS = (UP, LEFT, DOWN, RIGHT)


# # = wall, . = sun, o = Iran power-up, P = player, 1..4 = enemies, = = gate.
# Spaces inside the maze are traversable but do not contain collectibles.
PALACE_LEVEL = (
    "###################",
    "#o.......#.......o#",
    "#.##.###.#.###.##.#",
    "#.................#",
    "#.##.#.#####.#.##.#",
    "#....#...#...#....#",
    "####.### # ###.####",
    "   #.#       #.#   ",
    "####.# ##=## #.####",
    "    ..#1234#...    ",
    "####.# ##### #.####",
    "   #.#       #.#   ",
    "####.# ##### #.####",
    "#........#........#",
    "#.##.###.#.###.##.#",
    "#o.#.....P.....#.o#",
    "##.#.#.#####.#.#.##",
    "#....#...#...#....#",
    "#.######.#.######.#",
    "#.................#",
    "###################",
)


def generate_labyrinth(seed: int, width: int = 25, height: int = 17) -> tuple[str, ...]:
    """Create a deterministic, connected maze with a few arcade-style loops."""

    rng = random.Random(seed)
    grid = [["#" for _ in range(width)] for _ in range(height)]
    start = (1, 1)
    grid[start[1]][start[0]] = "."
    stack = [start]
    visited = {start}

    while stack:
        x, y = stack[-1]
        candidates = []
        for dx, dy in DIRECTIONS:
            nx, ny = x + dx * 2, y + dy * 2
            if 0 < nx < width - 1 and 0 < ny < height - 1 and (nx, ny) not in visited:
                candidates.append((nx, ny, dx, dy))
        if not candidates:
            stack.pop()
            continue
        nx, ny, dx, dy = rng.choice(candidates)
        grid[y + dy][x + dx] = "."
        grid[ny][nx] = "."
        visited.add((nx, ny))
        stack.append((nx, ny))

    # Open selected separators to create alternate routes and reduce dead ends.
    separators = []
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            if grid[y][x] != "#":
                continue
            horizontal = grid[y][x - 1] == "." and grid[y][x + 1] == "."
            vertical = grid[y - 1][x] == "." and grid[y + 1][x] == "."
            if horizontal or vertical:
                separators.append((x, y))
    rng.shuffle(separators)
    for x, y in separators[: max(18, len(separators) // 6)]:
        grid[y][x] = "."

    open_cells = [(x, y) for y in range(1, height - 1) for x in range(1, width - 1) if grid[y][x] == "."]

    def nearest(target: tuple[int, int], blocked: set[tuple[int, int]]) -> tuple[int, int]:
        return min((cell for cell in open_cells if cell not in blocked), key=lambda cell: abs(cell[0] - target[0]) + abs(cell[1] - target[1]))

    used: set[tuple[int, int]] = set()
    player = nearest((width // 2, height - 2), used)
    used.add(player)
    grid[player[1]][player[0]] = "P"

    ghost_targets = ((width // 2 - 2, height // 2), (width // 2, height // 2), (width // 2 + 2, height // 2), (width // 2, height // 2 + 2))
    for number, target in enumerate(ghost_targets, 1):
        spawn = nearest(target, used)
        used.add(spawn)
        grid[spawn[1]][spawn[0]] = str(number)

    for corner in ((1, 1), (width - 2, 1), (1, height - 2), (width - 2, height - 2)):
        power = nearest(corner, used)
        used.add(power)
        grid[power[1]][power[0]] = "o"

    return tuple("".join(row) for row in grid)


@dataclass(frozen=True)
class LevelSpec:
    name: str
    rows: tuple[str, ...]
    tile_blue: tuple[int, int, int]
    tile_accent: tuple[int, int, int]
    sandstone: tuple[int, int, int]


LEVELS = (
    LevelSpec("Kashi Palace", PALACE_LEVEL, (19, 92, 160), (34, 198, 195), (180, 111, 48)),
    LevelSpec("Copper Labyrinth", generate_labyrinth(37), (19, 116, 122), (69, 214, 175), (161, 91, 47)),
    LevelSpec("Crimson Citadel", generate_labyrinth(83), (116, 32, 69), (231, 91, 126), (191, 126, 58)),
)


class ScreenState(Enum):
    START = auto()
    PLAYING = auto()
    DYING = auto()
    WON = auto()
    GAME_OVER = auto()
    PAUSED = auto()
    LEVEL_CLEAR = auto()


class GhostMode(Enum):
    CHASE = auto()
    FRIGHTENED = auto()
    RESPAWNING = auto()


@dataclass
class Actor:
    """A moving entity whose position is measured in maze tiles."""

    x: float
    y: float
    direction: tuple[int, int] = STOP
    speed: float = 5.2

    @property
    def tile(self) -> tuple[int, int]:
        return (int(round(self.x)), int(round(self.y)))

    def at_tile_center(self, tolerance: float = 0.0001) -> bool:
        return abs(self.x - round(self.x)) < tolerance and abs(self.y - round(self.y)) < tolerance

    def snap_to_tile(self) -> None:
        self.x = float(round(self.x))
        self.y = float(round(self.y))


@dataclass
class Ghost(Actor):
    name: str = "Chaser"
    personality: str = "chaser"
    color: tuple[int, int, int] = (224, 65, 78)
    spawn: tuple[int, int] = (9, 9)
    scatter_target: tuple[int, int] = (1, 1)
    mode: GhostMode = GhostMode.CHASE
    respawn_timer: float = 0.0


class Maze:
    """Immutable wall layout plus mutable collectible locations."""

    def __init__(self, rows: Iterable[str]) -> None:
        self.rows = tuple(rows)
        self.width = len(self.rows[0])
        self.height = len(self.rows)
        self.player_spawn = (1, 1)
        self.ghost_spawns: dict[int, tuple[int, int]] = {}
        self.initial_pellets: set[tuple[int, int]] = set()
        self.initial_power: set[tuple[int, int]] = set()

        for y, row in enumerate(self.rows):
            for x, tile in enumerate(row):
                if tile == "P":
                    self.player_spawn = (x, y)
                elif tile.isdigit():
                    self.ghost_spawns[int(tile)] = (x, y)
                elif tile == ".":
                    self.initial_pellets.add((x, y))
                elif tile == "o":
                    self.initial_power.add((x, y))

        self.reset_collectibles()

    def reset_collectibles(self) -> None:
        self.pellets = set(self.initial_pellets)
        self.power_pellets = set(self.initial_power)

    def tile_at(self, x: int, y: int) -> str:
        # Horizontal tunnel cells wrap around.  Vertical movement never wraps.
        if y < 0 or y >= self.height:
            return "#"
        return self.rows[y][x % self.width]

    def passable(self, x: int, y: int, for_ghost: bool = False) -> bool:
        tile = self.tile_at(x, y)
        if tile == "#":
            return False
        if tile == "=" and not for_ghost:
            return False
        return True

    def neighbor(self, tile: tuple[int, int], direction: tuple[int, int]) -> tuple[int, int]:
        x = (tile[0] + direction[0]) % self.width
        y = tile[1] + direction[1]
        return (x, y)

    def legal_directions(self, tile: tuple[int, int], for_ghost: bool = False) -> list[tuple[int, int]]:
        legal = []
        for direction in DIRECTIONS:
            nx, ny = self.neighbor(tile, direction)
            if self.passable(nx, ny, for_ghost):
                legal.append(direction)
        return legal

    def distance(self, start: tuple[int, int], goal: tuple[int, int]) -> int:
        """Return shortest-path distance; used by the enemy decision system."""

        goal = (goal[0] % self.width, max(0, min(self.height - 1, goal[1])))
        frontier = deque([(start, 0)])
        visited = {start}
        while frontier:
            tile, distance = frontier.popleft()
            if tile == goal:
                return distance
            for direction in DIRECTIONS:
                nxt = self.neighbor(tile, direction)
                if nxt not in visited and self.passable(*nxt, for_ghost=True):
                    visited.add(nxt)
                    frontier.append((nxt, distance + 1))
        # Targets may point into a wall; Manhattan distance remains a useful
        # and deterministic fallback in that case.
        return abs(start[0] - goal[0]) + abs(start[1] - goal[1]) + 100


class AssetLibrary:
    """Loads the supplied artwork and prepares size-specific sprite caches."""

    def __init__(self) -> None:
        lion_sheet = self.load("lion-sprites.png")
        cleric_sheet = self.load("clerics-sheet.png")
        iran_power = self.load("iran-power.png")

        half_width = lion_sheet.get_width() // 2
        self.lion_sources = [
            self.crop(lion_sheet.subsurface((0, 0, half_width, lion_sheet.get_height())).copy()),
            self.crop(lion_sheet.subsurface((half_width, 0, lion_sheet.get_width() - half_width, lion_sheet.get_height())).copy()),
        ]

        cell_w = cleric_sheet.get_width() // 2
        cell_h = cleric_sheet.get_height() // 2
        self.cleric_sources = []
        for row in range(2):
            for column in range(2):
                cell = cleric_sheet.subsurface((column * cell_w, row * cell_h, cell_w, cell_h)).copy()
                self.cleric_sources.append(self.crop(cell))

        self.iran_power_source = self.crop(iran_power)
        self.cache: dict[tuple[str, int, int], pygame.Surface] = {}

    @staticmethod
    def load(filename: str) -> pygame.Surface:
        path = ASSET_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Required game asset is missing: {path}")
        return pygame.image.load(path).convert_alpha()

    @staticmethod
    def crop(surface: pygame.Surface) -> pygame.Surface:
        bounds = surface.get_bounding_rect(min_alpha=8)
        return surface.subsurface(bounds).copy() if bounds.width and bounds.height else surface

    @staticmethod
    def fit(surface: pygame.Surface, box_size: int) -> pygame.Surface:
        scale = min(box_size / surface.get_width(), box_size / surface.get_height())
        size = (max(1, round(surface.get_width() * scale)), max(1, round(surface.get_height() * scale)))
        return pygame.transform.smoothscale(surface, size)

    def lion(self, frame: int, size: int, face_left: bool = False) -> pygame.Surface:
        key = ("lion-left" if face_left else "lion-right", frame, size)
        if key not in self.cache:
            image = self.fit(self.lion_sources[frame], size)
            # The generated source artwork faces left (head and sword on the
            # left side), so only right-facing movement needs a horizontal flip.
            self.cache[key] = image if face_left else pygame.transform.flip(image, True, False)
        return self.cache[key]

    def cleric(self, index: int, size: int, frightened: bool = False) -> pygame.Surface:
        key = ("cleric-fright" if frightened else "cleric", index, size)
        if key not in self.cache:
            image = self.fit(self.cleric_sources[index], size)
            if frightened:
                image = image.copy()
                tint = pygame.Surface(image.get_size(), pygame.SRCALPHA)
                tint.fill((72, 120, 255, 150))
                image.blit(tint, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            self.cache[key] = image
        return self.cache[key]

    def iran_power(self, size: int) -> pygame.Surface:
        key = ("iran-power", 0, size)
        if key not in self.cache:
            self.cache[key] = self.fit(self.iran_power_source, size)
        return self.cache[key]

    def sun(self, size: int) -> pygame.Surface:
        """Return a cached glowing sun token drawn at the requested size."""

        key = ("sun", 0, size)
        if key in self.cache:
            return self.cache[key]
        canvas_size = max(12, int(size * 1.55))
        surface = pygame.Surface((canvas_size, canvas_size), pygame.SRCALPHA)
        center = canvas_size // 2
        outer = max(5, size // 2)
        inner = max(3, int(outer * 0.58))
        for index in range(12):
            angle = math.tau * index / 12
            start = (center + math.cos(angle) * inner, center + math.sin(angle) * inner)
            end = (center + math.cos(angle) * outer, center + math.sin(angle) * outer)
            pygame.draw.line(surface, (255, 161, 23, 235), start, end, max(1, size // 10))
        pygame.draw.circle(surface, (255, 129, 12, 90), (center, center), inner + max(2, size // 7))
        pygame.draw.circle(surface, (255, 188, 35), (center, center), inner)
        pygame.draw.circle(surface, (255, 238, 113), (center - inner // 4, center - inner // 4), max(2, inner // 2))
        pygame.draw.circle(surface, (196, 91, 11), (center, center), inner, max(1, size // 12))
        self.cache[key] = surface
        return surface


class Game:
    """Owns the full game loop, state machine, simulation, and rendering."""

    def __init__(self, headless: bool = False, fullscreen: bool = True) -> None:
        pygame.init()
        pygame.display.set_caption("Lion & Sun Maze")
        self.headless = headless
        self.fullscreen = fullscreen and not headless
        if headless:
            self.screen = pygame.display.set_mode((1600, 900), pygame.HIDDEN)
        elif self.fullscreen:
            desktop_size = pygame.display.get_desktop_sizes()[0]
            self.screen = pygame.display.set_mode(desktop_size, pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode(WINDOWED_SIZE, pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.refresh_fonts()
        self.assets = AssetLibrary()

        self.current_level = 0
        self.maze = Maze(LEVELS[self.current_level].rows)
        self.player = Actor(*self.maze.player_spawn, speed=5.35)
        self.ghosts: list[Ghost] = []
        self.state = ScreenState.START
        self.previous_state = ScreenState.PLAYING
        self.score = 0
        self.high_score = 0
        self.lives = 3
        self.desired_direction = LEFT
        self.frightened_timer = 0.0
        self.frightened_chain = 0
        self.super_mode = False
        self.super_banner_timer = 0.0
        self.state_timer = 0.0
        self.animation_time = 0.0
        self.running = True
        self.player_facing = RIGHT
        self.tile_size = 32
        self.board_origin = (0, 0)
        self.panel_rect = pygame.Rect(0, 0, 0, 0)
        self.background = pygame.Surface(self.screen.get_size())
        self.update_layout()
        self.reset_actors()

    def refresh_fonts(self) -> None:
        height = self.screen.get_height()
        self.font_tiny = pygame.font.Font(None, max(18, height // 48))
        self.font_small = pygame.font.Font(None, max(22, height // 36))
        self.font_medium = pygame.font.Font(None, max(32, height // 25))
        self.font_large = pygame.font.Font(None, max(64, height // 11))
        persian_path = pygame.font.match_font("segoeui") or pygame.font.match_font("arial")
        self.font_persian = pygame.font.Font(persian_path, max(24, height // 31))
        try:
            self.font_persian.set_script("Arab")
        except (AttributeError, pygame.error):
            pass

    def update_layout(self) -> None:
        width, height = self.screen.get_size()
        gap = max(14, min(26, width // 75))
        panel_width = max(250, min(350, int(width * 0.19)))
        usable_width = width - panel_width - gap - 40
        usable_height = height - 40
        self.tile_size = max(16, int(min(usable_width / self.maze.width, usable_height / self.maze.height)))
        board_width = self.maze.width * self.tile_size
        board_height = self.maze.height * self.tile_size
        group_width = board_width + gap + panel_width
        origin_x = max(12, (width - group_width) // 2)
        origin_y = max(12, (height - board_height) // 2)
        self.board_origin = (origin_x, origin_y)
        self.panel_rect = pygame.Rect(origin_x + board_width + gap, origin_y, panel_width, board_height)
        self.build_background()

    def build_background(self) -> None:
        width, height = self.screen.get_size()
        self.background = pygame.Surface((width, height))
        self.background.fill((6, 12, 27))
        motif = (18, 31, 56)
        step = max(72, height // 11)
        for y in range(-step, height + step, step):
            for x in range(-step, width + step, step):
                offset = step // 2 if (y // step) % 2 else 0
                center = (x + offset, y)
                pygame.draw.circle(self.background, motif, center, step // 3, 1)
                points = [(center[0], center[1] - step // 4), (center[0] + step // 4, center[1]),
                          (center[0], center[1] + step // 4), (center[0] - step // 4, center[1])]
                pygame.draw.polygon(self.background, motif, points, 1)

    # ------------------------------------------------------------------
    # Round and state management
    # ------------------------------------------------------------------

    def new_game(self) -> None:
        self.score = 0
        self.lives = 3
        self.frightened_timer = 0.0
        self.frightened_chain = 0
        self.super_mode = False
        self.super_banner_timer = 0.0
        self.current_level = 0
        self.load_level(self.current_level)
        self.state = ScreenState.PLAYING

    def load_level(self, index: int) -> None:
        self.current_level = index
        self.maze = Maze(LEVELS[index].rows)
        self.update_layout()
        self.reset_actors()

    def next_level(self) -> None:
        if self.current_level + 1 >= len(LEVELS):
            self.state = ScreenState.WON
            return
        self.load_level(self.current_level + 1)
        self.frightened_timer = 0.0
        self.frightened_chain = 0
        self.state = ScreenState.PLAYING

    def reset_actors(self) -> None:
        px, py = self.maze.player_spawn
        self.player = Actor(float(px), float(py), LEFT, 5.35)
        self.desired_direction = LEFT
        self.player_facing = LEFT

        definitions = (
            (1, "Chaser", "chaser", (224, 63, 77), (self.maze.width - 2, 1)),
            (2, "Ambusher", "ambusher", (236, 87, 168), (1, 1)),
            (3, "Wanderer", "random", (51, 193, 212), (self.maze.width - 2, self.maze.height - 2)),
            (4, "Shy", "shy", (246, 142, 52), (1, self.maze.height - 2)),
        )
        self.ghosts = []
        for number, name, personality, color, corner in definitions:
            spawn = self.maze.ghost_spawns[number]
            self.ghosts.append(
                Ghost(
                    float(spawn[0]),
                    float(spawn[1]),
                    UP if number == 4 else LEFT,
                    4.65,
                    name,
                    personality,
                    color,
                    spawn,
                    corner,
                )
            )

    def lose_life(self) -> None:
        if self.state != ScreenState.PLAYING or self.super_mode:
            return
        self.lives -= 1
        self.frightened_timer = 0.0
        self.state = ScreenState.DYING
        self.state_timer = 1.35

    def activate_super_mode(self) -> None:
        """Awaken the permanent Lion & Sun form after the third lost life."""

        self.super_mode = True
        self.super_banner_timer = 3.25
        self.score += 1000
        self.reset_actors()
        self.frightened_timer = 8.0
        self.frightened_chain = 0
        for ghost in self.ghosts:
            ghost.mode = GhostMode.FRIGHTENED
        self.state = ScreenState.PLAYING

    # ------------------------------------------------------------------
    # Input and simulation
    # ------------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return
        if event.type != pygame.KEYDOWN:
            if event.type == pygame.VIDEORESIZE and not self.fullscreen:
                self.screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
                self.refresh_fonts()
                self.update_layout()
            return

        if event.key in (pygame.K_ESCAPE, pygame.K_q):
            self.running = False
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE) and self.state in {
            ScreenState.START,
            ScreenState.WON,
            ScreenState.GAME_OVER,
        }:
            self.new_game()
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE) and self.state == ScreenState.LEVEL_CLEAR:
            self.next_level()
        elif event.key == pygame.K_F11:
            self.toggle_fullscreen()
        elif event.key == pygame.K_p and self.state in {ScreenState.PLAYING, ScreenState.PAUSED}:
            if self.state == ScreenState.PLAYING:
                self.previous_state = self.state
                self.state = ScreenState.PAUSED
            else:
                self.state = ScreenState.PLAYING
        elif event.key in (pygame.K_UP, pygame.K_w):
            self.desired_direction = UP
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.desired_direction = DOWN
        elif event.key in (pygame.K_LEFT, pygame.K_a):
            self.desired_direction = LEFT
        elif event.key in (pygame.K_RIGHT, pygame.K_d):
            self.desired_direction = RIGHT

    def toggle_fullscreen(self) -> None:
        if self.headless:
            return
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.screen = pygame.display.set_mode(pygame.display.get_desktop_sizes()[0], pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode(WINDOWED_SIZE, pygame.RESIZABLE)
        self.refresh_fonts()
        self.update_layout()

    def update(self, dt: float) -> None:
        self.animation_time += dt
        self.super_banner_timer = max(0.0, self.super_banner_timer - dt)
        if self.state == ScreenState.DYING:
            self.state_timer -= dt
            if self.state_timer <= 0:
                if self.lives <= 0:
                    self.activate_super_mode()
                else:
                    self.reset_actors()
                    self.state = ScreenState.PLAYING
            return
        if self.state != ScreenState.PLAYING:
            return

        self.frightened_timer = max(0.0, self.frightened_timer - dt)
        if self.frightened_timer == 0:
            for ghost in self.ghosts:
                if ghost.mode == GhostMode.FRIGHTENED:
                    ghost.mode = GhostMode.CHASE

        self.update_player(dt)
        self.collect_at_player()
        self.update_ghosts(dt)
        self.resolve_collisions()

        if not self.maze.pellets and not self.maze.power_pellets:
            self.high_score = max(self.high_score, self.score)
            self.state = ScreenState.LEVEL_CLEAR if self.current_level + 1 < len(LEVELS) else ScreenState.WON

    @staticmethod
    def is_reverse(first: tuple[int, int], second: tuple[int, int]) -> bool:
        return first[0] == -second[0] and first[1] == -second[1]

    def update_player(self, dt: float) -> None:
        player = self.player

        # Reversing is allowed immediately and feels much more responsive.
        if self.is_reverse(self.desired_direction, player.direction):
            player.direction = self.desired_direction

        if player.at_tile_center():
            player.snap_to_tile()
            tx, ty = player.tile
            desired_tile = self.maze.neighbor((tx, ty), self.desired_direction)
            if self.maze.passable(*desired_tile):
                player.direction = self.desired_direction
            forward_tile = self.maze.neighbor((tx, ty), player.direction)
            if not self.maze.passable(*forward_tile):
                player.direction = STOP

        if player.direction in (LEFT, RIGHT):
            self.player_facing = player.direction

        self.advance_actor(player, player.speed * dt)
        self.wrap_actor(player)

    def collect_at_player(self) -> None:
        tile = self.player.tile
        if tile in self.maze.pellets:
            self.maze.pellets.remove(tile)
            self.score += 10
        if tile in self.maze.power_pellets:
            self.maze.power_pellets.remove(tile)
            self.score += 50
            self.frightened_timer = 8.0
            self.frightened_chain = 0
            for ghost in self.ghosts:
                if ghost.mode != GhostMode.RESPAWNING:
                    ghost.mode = GhostMode.FRIGHTENED
                    ghost.direction = (-ghost.direction[0], -ghost.direction[1])

    def update_ghosts(self, dt: float) -> None:
        for ghost in self.ghosts:
            if ghost.mode == GhostMode.RESPAWNING:
                ghost.respawn_timer -= dt
                if ghost.respawn_timer <= 0:
                    ghost.x, ghost.y = map(float, ghost.spawn)
                    ghost.direction = UP
                    ghost.mode = GhostMode.CHASE
                continue

            if ghost.at_tile_center():
                ghost.snap_to_tile()
                self.choose_ghost_direction(ghost)

            speed = 3.35 if ghost.mode == GhostMode.FRIGHTENED else ghost.speed
            self.advance_actor(ghost, speed * dt)
            self.wrap_actor(ghost)

    @staticmethod
    def advance_actor(actor: Actor, distance: float) -> None:
        """Move an actor, stopping exactly on a crossed tile center.

        A simple tolerance-based snap can repeatedly pull an actor back to the
        center at high frame rates.  Detecting an actual crossing keeps motion
        stable regardless of frame duration and leaves turns perfectly aligned.
        """

        old_x, old_y = actor.x, actor.y
        actor.x += actor.direction[0] * distance
        actor.y += actor.direction[1] * distance

        if actor.direction[0] and abs(old_x - round(old_x)) > 0.0001:
            next_center = math.floor(old_x) if actor.direction[0] < 0 else math.ceil(old_x)
            crossed = actor.x <= next_center if actor.direction[0] < 0 else actor.x >= next_center
            if crossed:
                actor.x = float(next_center)
        if actor.direction[1] and abs(old_y - round(old_y)) > 0.0001:
            next_center = math.floor(old_y) if actor.direction[1] < 0 else math.ceil(old_y)
            crossed = actor.y <= next_center if actor.direction[1] < 0 else actor.y >= next_center
            if crossed:
                actor.y = float(next_center)

    def choose_ghost_direction(self, ghost: Ghost) -> None:
        tile = ghost.tile
        choices = self.maze.legal_directions(tile, for_ghost=True)
        reverse = (-ghost.direction[0], -ghost.direction[1])
        non_reverse = [choice for choice in choices if choice != reverse]
        if non_reverse:
            choices = non_reverse
        if not choices:
            ghost.direction = reverse
            return

        if ghost.mode == GhostMode.FRIGHTENED or ghost.personality == "random":
            ghost.direction = random.choice(choices)
            return

        target = self.ghost_target(ghost)
        ranked: list[tuple[int, float, tuple[int, int]]] = []
        for direction in choices:
            nxt = self.maze.neighbor(tile, direction)
            distance = self.maze.distance(nxt, target)
            ranked.append((distance, random.random(), direction))
        ghost.direction = min(ranked)[2]

    def ghost_target(self, ghost: Ghost) -> tuple[int, int]:
        px, py = self.player.tile
        dx, dy = self.player.direction
        if ghost.personality == "chaser":
            return (px, py)
        if ghost.personality == "ambusher":
            return (px + dx * 4, py + dy * 4)
        if ghost.personality == "shy":
            distance = abs(ghost.x - self.player.x) + abs(ghost.y - self.player.y)
            return ghost.scatter_target if distance < 6 else (px, py)
        return ghost.scatter_target

    def resolve_collisions(self) -> None:
        for ghost in self.ghosts:
            if ghost.mode == GhostMode.RESPAWNING:
                continue
            distance = math.hypot(self.player.x - ghost.x, self.player.y - ghost.y)
            # The tunnel crosses the horizontal edge; use its shorter distance.
            wrapped_dx = min(abs(self.player.x - ghost.x), self.maze.width - abs(self.player.x - ghost.x))
            distance = min(distance, math.hypot(wrapped_dx, self.player.y - ghost.y))
            if distance >= 0.68:
                continue
            if ghost.mode == GhostMode.FRIGHTENED or self.super_mode:
                base_score = 500 if self.super_mode else 200
                self.score += base_score * (2**self.frightened_chain)
                self.frightened_chain = min(3, self.frightened_chain + 1)
                ghost.mode = GhostMode.RESPAWNING
                ghost.respawn_timer = 2.0
            else:
                self.lose_life()
                break

    def wrap_actor(self, actor: Actor) -> None:
        if actor.x < -0.55:
            actor.x = self.maze.width - 0.45
        elif actor.x > self.maze.width - 0.45:
            actor.x = -0.55

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def tile_center(self, tile: tuple[float, float]) -> tuple[int, int]:
        return (
            int(self.board_origin[0] + (tile[0] + 0.5) * self.tile_size),
            int(self.board_origin[1] + (tile[1] + 0.5) * self.tile_size),
        )

    def blit_center(self, image: pygame.Surface, center: tuple[int, int]) -> None:
        self.screen.blit(image, image.get_rect(center=center))

    def draw(self) -> None:
        self.screen.blit(self.background, (0, 0))
        self.draw_maze()
        self.draw_collectibles()

        if self.state != ScreenState.DYING or int(self.state_timer * 10) % 2 == 0:
            self.draw_lion_sprite()
        for ghost in self.ghosts:
            if ghost.mode != GhostMode.RESPAWNING:
                self.draw_cleric_sprite(ghost)
        if self.super_mode:
            self.draw_super_screen_effect()
        self.draw_hud()

        if self.state == ScreenState.START:
            self.draw_overlay("LION & SUN MAZE", "Collect every sun. Reclaim all three maps.", "Press ENTER to start")
        elif self.state == ScreenState.PAUSED:
            self.draw_overlay("PAUSED", "", "Press P to continue")
        elif self.state == ScreenState.LEVEL_CLEAR:
            self.draw_overlay("MAP CLEARED!", f"Score: {self.score}", "Press ENTER for the next map")
        elif self.state == ScreenState.WON:
            self.draw_victory_overlay()
        elif self.state == ScreenState.GAME_OVER:
            self.draw_overlay("THE SUN RISES AGAIN", f"Score: {self.score}", "Press ENTER to return stronger")
        elif self.super_banner_timer > 0:
            self.draw_super_banner()

        pygame.display.flip()

    def draw_hud(self) -> None:
        panel = self.panel_rect
        spec = LEVELS[self.current_level]
        shadow = panel.move(7, 8)
        pygame.draw.rect(self.screen, (0, 0, 0), shadow, border_radius=24)
        pygame.draw.rect(self.screen, (10, 22, 43), panel, border_radius=24)
        pygame.draw.rect(self.screen, spec.sandstone, panel, max(3, self.tile_size // 12), border_radius=24)
        inner = panel.inflate(-16, -16)
        pygame.draw.rect(self.screen, spec.tile_blue, inner, 2, border_radius=18)

        center_x = panel.centerx
        y = panel.top + max(20, panel.height // 28)
        map_label = self.font_small.render(f"MAP {self.current_level + 1} / {len(LEVELS)}", True, GOLD_LIGHT)
        self.blit_center(map_label, (center_x, y + map_label.get_height() // 2))
        y += map_label.get_height() + 8
        name = self.font_medium.render(spec.name, True, INK)
        if name.get_width() > panel.width - 30:
            name = pygame.transform.smoothscale(name, (panel.width - 30, round(name.get_height() * (panel.width - 30) / name.get_width())))
        self.blit_center(name, (center_x, y + name.get_height() // 2))

        score_y = panel.top + int(panel.height * 0.23)
        label = self.font_tiny.render("SCORE", True, (141, 164, 195))
        value = self.font_large.render(f"{self.score:05d}", True, GOLD_LIGHT)
        if value.get_width() > panel.width - 28:
            value = pygame.transform.smoothscale(value, (panel.width - 28, round(value.get_height() * (panel.width - 28) / value.get_width())))
        self.blit_center(label, (center_x, score_y))
        self.blit_center(value, (center_x, score_y + label.get_height() + value.get_height() // 2))

        stats_y = panel.top + int(panel.height * 0.43)
        best = self.font_small.render(f"BEST   {self.high_score:05d}", True, (186, 202, 224))
        remaining = len(self.maze.pellets) + len(self.maze.power_pellets)
        total = len(self.maze.initial_pellets) + len(self.maze.initial_power)
        progress = self.font_small.render(f"SUNS   {total - remaining} / {total}", True, (186, 202, 224))
        self.blit_center(best, (center_x, stats_y))
        self.blit_center(progress, (center_x, stats_y + best.get_height() + 10))

        lives_y = panel.top + int(panel.height * 0.58)
        if self.super_mode:
            lives_label = self.font_small.render("LION & SUN MODE", True, (255, 191, 45))
            immortal = self.font_medium.render("IMMORTAL", True, (255, 235, 118))
            self.blit_center(lives_label, (center_x, lives_y))
            self.blit_center(immortal, (center_x, lives_y + immortal.get_height() + 10))
        else:
            lives_label = self.font_small.render("LIVES", True, (141, 164, 195))
            self.blit_center(lives_label, (center_x, lives_y))
            icon_size = max(36, min(62, panel.width // 5))
            icon = self.assets.lion(0, icon_size)
            spacing = min(icon_size, (panel.width - 42) // 3)
            first_x = center_x - spacing * (self.lives - 1) / 2
            for index in range(self.lives):
                self.blit_center(icon, (round(first_x + index * spacing), lives_y + icon_size // 2 + 14))

        controls_y = panel.top + int(panel.height * 0.78)
        pygame.draw.line(self.screen, spec.tile_accent, (panel.left + 25, controls_y - 20), (panel.right - 25, controls_y - 20), 2)
        lines = ("ARROWS / WASD  MOVE", "P  PAUSE", "F11  FULLSCREEN", "ESC  QUIT")
        for index, text in enumerate(lines):
            surface = self.font_tiny.render(text, True, (172, 192, 217))
            self.blit_center(surface, (center_x, controls_y + index * (surface.get_height() + 7)))

    def draw_maze(self) -> None:
        spec = LEVELS[self.current_level]
        ox, oy = self.board_origin
        t = self.tile_size
        board = pygame.Rect(ox, oy, self.maze.width * t, self.maze.height * t)
        pygame.draw.rect(self.screen, (0, 0, 0), board.move(7, 8), border_radius=max(8, t // 3))
        pygame.draw.rect(self.screen, (4, 19, 36), board, border_radius=max(8, t // 3))
        pygame.draw.rect(self.screen, spec.sandstone, board, max(3, t // 10), border_radius=max(8, t // 3))

        for y, row in enumerate(self.maze.rows):
            for x, tile in enumerate(row):
                if tile != "#":
                    continue
                rect = pygame.Rect(ox + x * t + 1, oy + y * t + 1, t - 2, t - 2)
                pygame.draw.rect(self.screen, (64, 35, 20), rect.move(2, 3), border_radius=max(3, t // 8))
                pygame.draw.rect(self.screen, spec.sandstone, rect, border_radius=max(3, t // 8))
                inner = rect.inflate(-max(4, t // 8), -max(4, t // 8))
                pygame.draw.rect(self.screen, (211, 157, 79), inner, 1, border_radius=max(2, t // 10))
                band = pygame.Rect(inner.left + 1, inner.top + 1, inner.width - 2, max(4, inner.height // 3))
                pygame.draw.rect(self.screen, spec.tile_blue, band, border_radius=max(2, t // 12))
                pygame.draw.line(self.screen, spec.tile_accent, (band.left + 2, band.bottom - 2), (band.right - 2, band.bottom - 2), max(1, t // 24))
                if t >= 34:
                    cx, cy = band.center
                    radius = max(2, t // 12)
                    pygame.draw.polygon(self.screen, GOLD_LIGHT, [(cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy)], 1)
                # Subtle brick joint.
                pygame.draw.line(self.screen, (117, 68, 34), (rect.left + 2, rect.centery), (rect.right - 2, rect.centery), 1)

        for y, row in enumerate(self.maze.rows):
            for x, tile in enumerate(row):
                if tile == "=":
                    cx, cy = self.tile_center((x, y))
                    pygame.draw.line(self.screen, (255, 139, 205), (cx - t // 2 + 5, cy), (cx + t // 2 - 5, cy), max(4, t // 9))

    def draw_collectibles(self) -> None:
        regular = self.assets.sun(max(14, int(self.tile_size * 0.43)))
        for tile in self.maze.pellets:
            self.blit_center(regular, self.tile_center(tile))
        pulse = 1.0 + 0.10 * math.sin(self.animation_time * 6.0)
        power = self.assets.iran_power(max(46, int(self.tile_size * 1.42 * pulse)))
        for tile in self.maze.power_pellets:
            self.blit_center(power, self.tile_center(tile))

    def draw_lion_sprite(self) -> None:
        center = self.tile_center((self.player.x, self.player.y))
        size = max(74, int(self.tile_size * 2.55))
        moving = self.player.direction != STOP and self.state == ScreenState.PLAYING
        frame = int(self.animation_time * 7.5) % 2 if moving else 0
        image = self.assets.lion(frame, size, self.player_facing == LEFT)
        if self.super_mode:
            self.draw_super_aura(center, size)
        shadow = pygame.Rect(0, 0, int(image.get_width() * 0.72), max(6, int(image.get_height() * 0.18)))
        shadow.center = (center[0], center[1] + int(image.get_height() * 0.38))
        shadow_surface = pygame.Surface(shadow.size, pygame.SRCALPHA)
        pygame.draw.ellipse(shadow_surface, (0, 0, 0, 105), shadow_surface.get_rect())
        self.screen.blit(shadow_surface, shadow)
        self.blit_center(image, center)

    def draw_super_aura(self, center: tuple[int, int], size: int) -> None:
        """Draw the animated sun that completes the Lion & Sun emblem."""

        aura_size = int(size * 2.15)
        aura = pygame.Surface((aura_size, aura_size), pygame.SRCALPHA)
        c = aura_size // 2
        rotation = self.animation_time * 0.65
        inner = int(size * 0.34)
        outer = int(size * 0.78)
        for index in range(32):
            angle = math.tau * index / 32 + rotation
            length = outer if index % 2 == 0 else int(outer * 0.78)
            start = (c + math.cos(angle) * inner, c + math.sin(angle) * inner)
            end = (c + math.cos(angle) * length, c + math.sin(angle) * length)
            pygame.draw.line(aura, (255, 181, 34, 145), start, end, max(2, size // 28))
        pygame.draw.circle(aura, (255, 126, 10, 58), (c, c), int(size * 0.48))
        pygame.draw.circle(aura, (255, 201, 55, 85), (c, c), inner)
        pygame.draw.circle(aura, (255, 239, 132, 180), (c, c), int(inner * 0.74), max(2, size // 35))

        for index in range(14):
            angle = rotation * 1.9 + index * math.tau / 14
            orbit = size * (0.50 + 0.20 * math.sin(self.animation_time * 2.2 + index))
            x = int(c + math.cos(angle) * orbit)
            y = int(c + math.sin(angle) * orbit)
            radius = max(2, size // 30 + index % 3)
            pygame.draw.circle(aura, (255, 231, 104, 220), (x, y), radius)
        self.blit_center(aura, center)

    def draw_super_screen_effect(self) -> None:
        width, height = self.screen.get_size()
        glow = pygame.Surface((width, height), pygame.SRCALPHA)
        pulse = int(55 + 22 * math.sin(self.animation_time * 4.0))
        border = max(6, height // 90)
        pygame.draw.rect(glow, (255, 177, 31, pulse), glow.get_rect(), border, border_radius=20)
        pygame.draw.rect(glow, (255, 232, 115, pulse // 2), glow.get_rect().inflate(-border * 2, -border * 2), 2, border_radius=18)
        self.screen.blit(glow, (0, 0))

    def draw_super_banner(self) -> None:
        width, height = self.screen.get_size()
        banner_width = min(880, width - 80)
        banner_height = max(100, height // 8)
        banner = pygame.Surface((banner_width, banner_height), pygame.SRCALPHA)
        alpha = min(235, int(self.super_banner_timer * 125))
        pygame.draw.rect(banner, (80, 36, 5, alpha), banner.get_rect(), border_radius=24)
        pygame.draw.rect(banner, (255, 196, 50, alpha), banner.get_rect(), 4, border_radius=24)
        title = self.font_medium.render("THE LION & SUN AWAKENS", True, (255, 241, 164))
        # SDL_ttf shapes Arabic-script glyphs but lays them out left-to-right;
        # reversing the logical string produces the correct visual RTL order.
        persian_text = "هرگز شیر و خورشید بر ملا ها شکست نخواهد خورد"
        subtitle = self.font_persian.render(persian_text[::-1], True, INK)
        if subtitle.get_width() > banner_width - 40:
            new_width = banner_width - 40
            subtitle = pygame.transform.smoothscale(subtitle, (new_width, round(subtitle.get_height() * new_width / subtitle.get_width())))
        banner.blit(title, title.get_rect(center=(banner_width // 2, banner_height * 2 // 5)))
        banner.blit(subtitle, subtitle.get_rect(center=(banner_width // 2, banner_height * 3 // 4)))
        banner.set_alpha(alpha)
        self.screen.blit(banner, banner.get_rect(center=(width // 2, height // 5)))

    def draw_cleric_sprite(self, ghost: Ghost) -> None:
        center = self.tile_center((ghost.x, ghost.y))
        size = max(62, int(self.tile_size * 1.86))
        frightened = ghost.mode == GhostMode.FRIGHTENED
        flashing = frightened and self.frightened_timer < 2.0 and int(self.frightened_timer * 8) % 2 == 0
        image = self.assets.cleric(self.ghosts.index(ghost), size, frightened and not flashing)
        aura_size = int(size * (1.08 + 0.06 * math.sin(self.animation_time * 5 + self.ghosts.index(ghost))))
        aura = pygame.Surface((aura_size, aura_size), pygame.SRCALPHA)
        aura_color = (41, 103, 255, 42) if frightened else (229, 28, 45, 42)
        pygame.draw.circle(aura, aura_color, (aura_size // 2, aura_size // 2), aura_size // 2)
        pygame.draw.circle(aura, (*aura_color[:3], 80), (aura_size // 2, aura_size // 2), max(3, aura_size // 3), 2)
        self.blit_center(aura, center)
        shadow = pygame.Rect(0, 0, int(size * 0.66), max(6, int(size * 0.13)))
        shadow.center = (center[0], center[1] + int(size * 0.34))
        shadow_surface = pygame.Surface(shadow.size, pygame.SRCALPHA)
        pygame.draw.ellipse(shadow_surface, (0, 0, 0, 100), shadow_surface.get_rect())
        self.screen.blit(shadow_surface, shadow)
        self.blit_center(image, center)

    def draw_victory_overlay(self) -> None:
        """Render the victory card requested by the user.

        The supplied PNG is fully transparent, so the card is rendered at
        native screen resolution instead of stretching an empty bitmap.
        """

        width, height = self.screen.get_size()
        shade = pygame.Surface((width, height), pygame.SRCALPHA)
        shade.fill((3, 8, 18, 225))
        self.screen.blit(shade, (0, 0))

        panel = pygame.Rect(0, 0, min(940, width - 70), min(600, height - 60))
        panel.center = (width // 2, height // 2)
        pygame.draw.rect(self.screen, (0, 0, 0), panel.move(10, 12), border_radius=34)
        pygame.draw.rect(self.screen, (15, 33, 49), panel, border_radius=34)
        pygame.draw.rect(self.screen, (229, 157, 32), panel, 6, border_radius=34)
        pygame.draw.rect(self.screen, (35, 152, 94), panel.inflate(-22, -22), 3, border_radius=26)

        emblem_center = (panel.centerx, panel.top + int(panel.height * 0.26))
        self.draw_super_aura(emblem_center, min(220, panel.height // 3))
        emblem = self.assets.iran_power(min(190, panel.height // 3))
        self.blit_center(emblem, emblem_center)

        victory = self.font_medium.render("ALL THREE MAPS CLEARED", True, (179, 220, 188))
        message = self.font_large.render("PAHLAVI IS PROUD OF YOU", True, GOLD_LIGHT)
        if message.get_width() > panel.width - 60:
            new_width = panel.width - 60
            message = pygame.transform.smoothscale(message, (new_width, round(message.get_height() * new_width / message.get_width())))
        score = self.font_small.render(f"FINAL SCORE   {self.score:05d}", True, INK)
        prompt = self.font_small.render("Press ENTER to play again", True, (88, 216, 174))
        self.blit_center(victory, (panel.centerx, panel.top + int(panel.height * 0.52)))
        self.blit_center(message, (panel.centerx, panel.top + int(panel.height * 0.65)))
        self.blit_center(score, (panel.centerx, panel.top + int(panel.height * 0.79)))
        if int(self.animation_time * 2) % 2 == 0:
            self.blit_center(prompt, (panel.centerx, panel.top + int(panel.height * 0.90)))

    def draw_overlay(self, title: str, subtitle: str, prompt: str) -> None:
        width, height = self.screen.get_size()
        shade = pygame.Surface((width, height), pygame.SRCALPHA)
        shade.fill((4, 5, 14, 205))
        self.screen.blit(shade, (0, 0))

        panel_width = min(820, width - 80)
        panel_height = min(370, height - 80)
        panel = pygame.Rect(0, 0, panel_width, panel_height)
        panel.center = (width // 2, height // 2)
        spec = LEVELS[self.current_level]
        pygame.draw.rect(self.screen, (0, 0, 0), panel.move(8, 9), border_radius=30)
        pygame.draw.rect(self.screen, (12, 25, 49), panel, border_radius=30)
        pygame.draw.rect(self.screen, spec.sandstone, panel, 5, border_radius=30)
        pygame.draw.rect(self.screen, spec.tile_blue, panel.inflate(-20, -20), 2, border_radius=22)

        title_surface = self.font_large.render(title, True, GOLD_LIGHT)
        if title_surface.get_width() > panel.width - 50:
            new_width = panel.width - 50
            title_surface = pygame.transform.smoothscale(title_surface, (new_width, round(title_surface.get_height() * new_width / title_surface.get_width())))
        subtitle_surface = self.font_small.render(subtitle, True, INK)
        prompt_surface = self.font_small.render(prompt, True, spec.tile_accent)
        self.blit_center(title_surface, (width // 2, panel.top + int(panel.height * 0.31)))
        self.blit_center(subtitle_surface, (width // 2, panel.top + int(panel.height * 0.56)))
        if int(self.animation_time * 2) % 2 == 0 or self.state == ScreenState.PAUSED:
            self.blit_center(prompt_surface, (width // 2, panel.top + int(panel.height * 0.76)))

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        while self.running:
            dt = min(self.clock.tick(FPS) / 1000.0, 0.05)
            for event in pygame.event.get():
                self.handle_event(event)
            self.update(dt)
            self.draw()
        pygame.quit()


def validate_level(spec: LevelSpec) -> int:
    """Validate one map and return its collectible count."""

    rows = spec.rows
    assert rows, f"{spec.name}: the level cannot be empty."
    assert all(len(row) == len(rows[0]) for row in rows), f"{spec.name}: maze rows have different widths."
    assert sum(row.count("P") for row in rows) == 1, f"{spec.name}: exactly one player spawn is required."
    assert all(sum(row.count(str(number)) for row in rows) == 1 for number in range(1, 5)), f"{spec.name}: missing ghost spawn."
    maze = Maze(rows)
    assert maze.initial_pellets, f"{spec.name}: the level needs sun tokens."
    assert len(maze.initial_power) == 4, f"{spec.name}: four Iran power-ups are required."
    reachable = {maze.player_spawn}
    frontier = deque([maze.player_spawn])
    while frontier:
        tile = frontier.popleft()
        for direction in DIRECTIONS:
            nxt = maze.neighbor(tile, direction)
            if nxt not in reachable and maze.passable(*nxt):
                reachable.add(nxt)
                frontier.append(nxt)
    unreachable = (maze.initial_pellets | maze.initial_power) - reachable
    assert not unreachable, f"{spec.name}: unreachable collectibles: {sorted(unreachable)}"

    # Every enemy must be able to leave its house and reach the player.  This
    # catches accidental sealed corridors when the ASCII level is customized.
    for number, spawn in maze.ghost_spawns.items():
        ghost_reachable = {spawn}
        ghost_frontier = deque([spawn])
        while ghost_frontier:
            tile = ghost_frontier.popleft()
            for direction in DIRECTIONS:
                nxt = maze.neighbor(tile, direction)
                if nxt not in ghost_reachable and maze.passable(*nxt, for_ghost=True):
                    ghost_reachable.add(nxt)
                    ghost_frontier.append(nxt)
        assert maze.player_spawn in ghost_reachable, f"{spec.name}: ghost {number} cannot reach the player."
    return len(maze.initial_pellets) + len(maze.initial_power)


def validate_levels() -> int:
    return sum(validate_level(spec) for spec in LEVELS)


def main() -> None:
    parser = argparse.ArgumentParser(description="A full-screen Lion & Sun arcade maze game.")
    parser.add_argument("--self-test", action="store_true", help="validate all three maps and exit")
    parser.add_argument("--windowed", action="store_true", help="start in a resizable window instead of full screen")
    args = parser.parse_args()
    total = validate_levels()
    if args.self_test:
        print(f"Self-test passed: {len(LEVELS)} maps and {total} reachable collectible items.")
        return
    Game(fullscreen=not args.windowed).run()


if __name__ == "__main__":
    main()
