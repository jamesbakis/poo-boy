"""Poo Boy - bounce on poos, chain combos, survive the speed-up.

Controls:
    Up / Down + Enter         choose from the menu
    Left / Right (or A / D)   move
    Space / Up / W            jump
    R / Space                 restart after game over
    Esc                       back to the menu (quit from the menu)

Multiplayer: one player picks "Host a LAN game", everyone else on the same
network picks "Join a LAN game". Up to 4 players share the same poos; each has
their own score, combo and lives, and the highest score wins.
"""

import base64
import io
import math
import os
import random
import sys

import pygame

import net

FROZEN = getattr(sys, "frozen", False)  # True when running as a PyInstaller .exe
if not FROZEN:
    # Keep packed_assets.py in sync with the Assets folder (see pack_assets.py).
    import pack_assets
    pack_assets.update()
try:
    import packed_assets  # the images embedded as code, so every PyInstaller build includes them
except ImportError:
    packed_assets = None

# --- Paths ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
SPRITE_DIR = os.path.join(ASSETS_DIR, "Sprites")
BG_PATH = os.path.join(ASSETS_DIR, "Background", "background1.png")
# a .exe unpacks into a temporary folder that is deleted on exit, so keep the high score next to the .exe
HIGHSCORE_FILE = os.path.join(os.path.dirname(sys.executable) if FROZEN else BASE_DIR, "highscore.txt")

# --- Config -----------------------------------------------------------------
BG_SCALE = 2.5                       # background1.png is 480x270
WIDTH, HEIGHT = int(480 * BG_SCALE), int(270 * BG_SCALE)
GROUND_Y = int(250 * BG_SCALE)       # top of the tiled floor in the background
FPS = 60

GRAVITY = 2200.0          # px/s^2
MOVE_SPEED = 380.0        # px/s
JUMP_HEIGHT = 190.0       # how high your feet rise on a full jump (a bit less in practice, ~182)
JUMP_VEL = -math.sqrt(2 * GRAVITY * JUMP_HEIGHT)
BOUNCE_VEL = JUMP_VEL     # bouncing off a poo goes exactly as high as a full jump

PLAYER_SIZE = 72

# Poo types: sprite file, draw size, hitbox inset (left/right, top)
POO_TYPES = {
    "ground": {"file": "ground-poo-guy-1.png", "size": (110, 56), "inset": (14, 8)},
    "high":   {"file": "high-up-poo-guy-1.png", "size": (84, 62), "inset": (10, 6)},
    "fly":    {"file": "fly-poo-guy-1.png", "size": (120, 54), "inset": (14, 8)},
    "gold":   {"file": "gold_poo_spritesheet.png", "size": (74, 66), "inset": (10, 10), "frames": 5},
}

# Fixed lanes. "land" is how far above the floor you stand when you're on top of that
# lane's poo; "unlock" is how many seconds in that lane starts spawning poos.
# With a jump/bounce height of ~182px:
#   floor -> lane 1 (48) and lane 2 (115)        jump from the floor
#   lane 1 (48) or lane 2 (115) -> lane 3 (205)  bounce needs 157 / 90
#   lane 3 (205) -> lane 4 (320, gold)           bounce needs 115
#   floor -> lane 3 needs 205 and lane 2 -> lane 4 needs 205, so neither works
# "points" is what a stomp is worth, multiplied by your current combo.
LANES = [
    {"land": 48,  "kind": "ground", "unlock": 0,  "points": 100},
    {"land": 115, "kind": "high",   "unlock": 12, "points": 130},
    {"land": 205, "kind": "fly",    "unlock": 24, "points": 170},
    {"land": 320, "kind": "gold",   "unlock": 36, "points": 300},
]

START_SPEED_MIN, START_SPEED_MAX = 130.0, 230.0
SPEED_GAIN_PER_SEC = 2.0  # the speed range rises this much every second
SPEED_CAP = 950.0
SPEED_TAPER_START = 350.0   # once the average speed (the HUD "Speed") passes this, it rises more and more slowly
SPEED_TAPER_SOFTNESS = 100.0  # bigger = gentler taper
START_SPAWN_INTERVAL = 1.4
MIN_SPAWN_INTERVAL = 0.45
SPAWN_SPEEDUP_PER_SEC = 0.006  # seconds shaved off the spawn gap every second
# Extra spawner for the bottom two lanes, so there are more stepping stones up to lane 3.
# It starts when lane 3 unlocks and its gap shrinks faster than the main spawner's.
LOW_LANE_COUNT = 2
LOW_START_INTERVAL = 4.0
LOW_MIN_INTERVAL = 1.5
LOW_SPEEDUP_PER_SEC = 0.015

START_LIVES = 3
INVULN_TIME = 1.5

MAX_PLAYERS = 4
# (sprite tint, name colour) for P1..P4
PLAYER_COLORS = [
    ((255, 255, 255), (255, 230, 80)),
    ((255, 175, 175), (255, 110, 110)),
    ((175, 205, 255), (120, 175, 255)),
    ((195, 255, 175), (120, 225, 100)),
]

MENU = ["Single player", "Host a LAN game", "Join a LAN game", "Quit"]

WHITE = (255, 255, 255)
BLACK = (25, 25, 25)
RED = (220, 50, 60)
YELLOW = (255, 230, 80)


# --- Asset loading ----------------------------------------------------------
def load_image(path):
    if os.path.exists(path):
        return pygame.image.load(path).convert_alpha()
    # Not on disk (e.g. inside a .exe built without the Assets folder): use the embedded copy.
    rel = os.path.relpath(path, ASSETS_DIR).replace(os.sep, "/")
    data = packed_assets.FILES.get(rel) if packed_assets else None
    if data is None:
        raise FileNotFoundError(f"Missing image {path} (and no embedded copy of {rel!r})")
    return pygame.image.load(io.BytesIO(base64.b64decode(data)), rel).convert_alpha()


def crop_frames(sheet, frame_count, frame_w=None):
    """Split a horizontal sheet into frames, all cropped to one shared bounding box
    so the animation doesn't wobble."""
    frame_w = frame_w or sheet.get_width() // frame_count
    frames = [sheet.subsurface((i * frame_w, 0, frame_w, sheet.get_height())) for i in range(frame_count)]
    box = frames[0].get_bounding_rect()
    for f in frames[1:]:
        box = box.union(f.get_bounding_rect())
    return [f.subsurface(box).copy() for f in frames]


def load_poo_frames(kind):
    spec = POO_TYPES[kind]
    sheet = load_image(os.path.join(SPRITE_DIR, spec["file"]))
    frames = crop_frames(sheet, spec.get("frames", 1))
    return [pygame.transform.smoothscale(f, spec["size"]) for f in frames]


def load_player_frames():
    sheet = load_image(os.path.join(SPRITE_DIR, "poo-boy-frame1.png"))
    # 5 frames of 100x100; the last two are marked "Dont use"
    frames = crop_frames(sheet, 5, frame_w=100)[:3]
    return [pygame.transform.smoothscale(f, (PLAYER_SIZE, PLAYER_SIZE)) for f in frames]


def tinted(surf, color):
    s = surf.copy()
    s.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
    return s


# --- Entities ---------------------------------------------------------------
class Poo:
    def __init__(self, lane, from_left, speed, frames, kind):
        self.id = 0
        self.kind = kind
        self.lane = lane
        self.lane_idx = LANES.index(lane)
        self.w, self.h = POO_TYPES[kind]["size"]
        self.inset = POO_TYPES[kind]["inset"]
        self.from_left = from_left
        self.vx = speed if from_left else -speed
        self.x = float(-self.w if from_left else WIDTH)
        # place the poo so its stompable top sits at the lane's landing height, never below the floor
        self.y = float(min(GROUND_Y - lane["land"] - self.inset[1], GROUND_Y - self.h))
        # sprites face right; flip the ones walking left
        self.frames = frames if from_left else [pygame.transform.flip(f, True, False) for f in frames]
        self.anim = random.random() * 10
        self.squished = 0.0
        self.dead = False

    def hitbox(self):
        ix, iy = self.inset
        return pygame.Rect(int(self.x + ix), int(self.y + iy), self.w - 2 * ix, self.h - iy)

    def update(self, dt):
        self.anim += dt
        if self.squished > 0:
            self.squished -= dt
            if self.squished <= 0:
                self.dead = True
            return
        self.x += self.vx * dt
        if self.x > WIDTH + self.w or self.x < -self.w * 2:
            self.dead = True

    def draw(self, screen):
        frame = self.frames[int(self.anim * 10) % len(self.frames)]
        if self.squished > 0:
            t = self.squished / 0.25
            h = max(4, int(self.h * 0.45 * t))
            img = pygame.transform.smoothscale(frame, (int(self.w * 1.3), h))
            img.set_alpha(int(255 * t))
            screen.blit(img, (self.x - self.w * 0.15, self.y + self.h - h))
            return
        bob = 0 if self.kind in ("ground", "gold") else math.sin(self.anim * 6) * 2.5
        screen.blit(frame, (self.x, self.y + bob))


class Player:
    def __init__(self, frames, pid=0):
        self.pid = pid
        tint, self.color = PLAYER_COLORS[pid % len(PLAYER_COLORS)]
        if tint != WHITE:
            frames = [tinted(f, tint) for f in frames]
        self.frames_r = frames
        self.frames_l = [pygame.transform.flip(f, True, False) for f in frames]
        self.jumps_seen = 0  # highest jump-press count received from this player's client
        self.reset()

    @property
    def name(self):
        return f"P{self.pid + 1}"

    def reset(self, x=None):
        self.x = WIDTH / 2 - PLAYER_SIZE / 2 if x is None else x
        self.y = float(GROUND_Y - PLAYER_SIZE)
        self.vx = 0.0
        self.vy = 0.0
        self.on_ground = True
        self.facing_right = True
        self.invuln = 0.0
        self.anim = 0.0
        self.jump_cut = False  # True for ground jumps: letting go of jump early gives a short hop
        self.prev_bottom = self.y + PLAYER_SIZE
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.lives = START_LIVES
        self.alive = True
        # input
        self.dir = 0
        self.held = False
        self.jump_req = False

    def rect(self):
        # the round body doesn't fill the whole frame
        return pygame.Rect(int(self.x + 12), int(self.y + 10), PLAYER_SIZE - 24, PLAYER_SIZE - 10)

    def jump(self):
        if self.on_ground:
            self.vy = JUMP_VEL
            self.on_ground = False
            self.jump_cut = True

    def update_anim(self, dt):
        if self.vx != 0 or not self.on_ground:
            self.anim += dt
        else:
            self.anim = 0

    def draw(self, screen):
        if self.invuln > 0 and int(self.invuln * 12) % 2 == 0:
            return
        frames = self.frames_r if self.facing_right else self.frames_l
        if self.on_ground:
            img = frames[int(self.anim * 10) % len(frames)]
        else:
            img = frames[1] if self.vy < 0 else frames[2]
        # squash & stretch based on vertical speed
        stretch = max(-0.18, min(0.18, -self.vy / 4000))
        w = int(PLAYER_SIZE * (1 - stretch))
        h = int(PLAYER_SIZE * (1 + stretch))
        img = pygame.transform.smoothscale(img, (w, h))
        screen.blit(img, (self.x + (PLAYER_SIZE - w) / 2, self.y + PLAYER_SIZE - h))


class Popup:
    def __init__(self, text, x, y, color, font, life=0.9):
        self.surf = font.render(text, True, color)
        self.shadow = font.render(text, True, BLACK)
        self.x, self.y = x, y
        self.life = life

    def update(self, dt):
        self.life -= dt
        self.y -= 60 * dt

    def draw(self, screen):
        alpha = max(0, int(255 * min(1, self.life / 0.5)))
        for surf, off in ((self.shadow, 2), (self.surf, 0)):
            s = surf.copy()
            s.set_alpha(alpha)
            screen.blit(s, (self.x - s.get_width() / 2 + off, self.y + off))


# --- Game -------------------------------------------------------------------
class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Poo Boy")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.frame = pygame.Surface((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.font_big = pygame.font.SysFont("comicsansms,arial", 72, bold=True)
        self.font_med = pygame.font.SysFont("comicsansms,arial", 34, bold=True)
        self.font_small = pygame.font.SysFont("comicsansms,arial", 22, bold=True)

        self.background = pygame.transform.scale(load_image(BG_PATH), (WIDTH, HEIGHT))
        self.poo_frames = {kind: load_poo_frames(kind) for kind in POO_TYPES}
        self.player_frames = load_player_frames()
        self.highscore = self.load_highscore()

        # mode: "single", "host" or "client"
        self.mode = "single"
        self.host = self.client = self.discovery = None
        self.local_pid = 0
        self.players = [Player(self.player_frames, 0)]
        self.jumps_sent = 0
        self.lan_ip = ""
        self.found_hosts = []
        self.menu_index = 0
        self.join_index = 0
        self.typing = False
        self.ip_text = ""
        self.message = ""
        self.state = "menu"  # menu, join, lobby, playing, gameover
        self.reset()

    # --- helpers ---------------------------------------------------------------
    @property
    def player(self):
        """The player controlled on this computer."""
        return self.get_player(self.local_pid)

    def get_player(self, pid):
        for p in self.players:
            if p.pid == pid:
                return p
        return None

    @property
    def multiplayer(self):
        return self.mode != "single"

    def load_highscore(self):
        try:
            with open(HIGHSCORE_FILE) as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def save_highscore(self):
        try:
            with open(HIGHSCORE_FILE, "w") as f:
                f.write(str(self.highscore))
        except OSError:
            pass

    def reset(self):
        self.poos = []
        self.popups = []
        self.events = []  # things the host tells clients about (popups, hits)
        self.elapsed = 0.0
        self.spawn_timer = 0.5
        self.low_timer = 1.0
        self.shake = 0.0
        self.next_poo_id = 0
        self.players.sort(key=lambda p: p.pid)
        n = len(self.players)
        for i, p in enumerate(self.players):
            p.reset(WIDTH * (i + 1) / (n + 1) - PLAYER_SIZE / 2)

    def add_popup(self, text, x, y, color, size="m", life=0.9):
        font = self.font_med if size == "m" else self.font_small
        self.popups.append(Popup(text, x, y, color, font, life))
        if self.mode == "host":
            self.events.append(["pop", text, round(x), round(y), list(color), size, life])

    def read_local_input(self):
        keys = pygame.key.get_pressed()
        direction = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (keys[pygame.K_LEFT] or keys[pygame.K_a])
        held = bool(keys[pygame.K_SPACE] or keys[pygame.K_UP] or keys[pygame.K_w])
        return direction, held

    # --- difficulty ------------------------------------------------------------
    def speed_range(self):
        gain = SPEED_GAIN_PER_SEC * self.elapsed
        lo = START_SPEED_MIN + gain
        hi = START_SPEED_MAX + gain * 1.3
        mid = (lo + hi) / 2
        if mid > SPEED_TAPER_START:
            # past the taper point, extra speed only grows logarithmically
            excess = mid - SPEED_TAPER_START
            tapered = SPEED_TAPER_START + SPEED_TAPER_SOFTNESS * math.log1p(excess / SPEED_TAPER_SOFTNESS)
            lo, hi = lo * tapered / mid, hi * tapered / mid
        return min(SPEED_CAP * 0.7, lo), min(SPEED_CAP, hi)

    def spawn_interval(self):
        return max(MIN_SPAWN_INTERVAL, START_SPAWN_INTERVAL - self.elapsed * SPAWN_SPEEDUP_PER_SEC)

    def low_lane_interval(self):
        since = self.elapsed - LANES[LOW_LANE_COUNT]["unlock"]
        return max(LOW_MIN_INTERVAL, LOW_START_INTERVAL - since * LOW_SPEEDUP_PER_SEC)

    def unlocked_lanes(self):
        return [lane for lane in LANES if self.elapsed >= lane["unlock"]]

    def spawn_poo(self, lane=None):
        lane = lane or random.choice(self.unlocked_lanes())
        from_left = random.random() < 0.5
        # don't spawn on top of a poo that has only just entered the same lane from the same side
        for p in self.poos:
            if p.lane is lane and p.from_left == from_left and (
                (from_left and p.x < p.w * 1.2) or (not from_left and p.x > WIDTH - p.w * 2.2)
            ):
                return
        kind = lane["kind"]
        speed = random.uniform(*self.speed_range())
        poo = Poo(lane, from_left, speed, self.poo_frames[kind], kind)
        self.next_poo_id += 1
        poo.id = self.next_poo_id
        self.poos.append(poo)

    # --- mode switching --------------------------------------------------------
    def start_single(self):
        self.leave_network()
        self.reset()
        self.state = "playing"

    def start_host(self):
        try:
            host = net.Host(MAX_PLAYERS)
        except OSError as e:
            self.message = f"Couldn't start hosting: {e.strerror or e}"
            return
        self.host = host
        self.mode = "host"
        self.lan_ip = host.ip
        self.local_pid = 0
        self.players = [Player(self.player_frames, 0)]
        self.reset()
        self.state = "lobby"

    def start_join(self):
        try:
            self.discovery = net.Discovery()
        except OSError:
            self.discovery = None
            self.message = "Can't search for games on this computer, but you can still type an IP."
        self.found_hosts = []
        self.join_index = 0
        self.typing = False
        self.ip_text = ""
        self.state = "join"

    def connect(self, ip):
        try:
            client = net.Client(ip)
        except OSError as e:
            self.message = f"Couldn't connect to {ip}: {e.strerror or e}"
            return
        if self.discovery:
            self.discovery.close()
            self.discovery = None
        self.client = client
        self.mode = "client"
        self.local_pid = -1  # until the host tells us
        self.players = []
        self.jumps_sent = 0
        self.message = ""
        self.reset()
        self.state = "lobby"

    def leave_network(self, message=""):
        """Close any connection and go back to a fresh single-player setup."""
        for obj in (self.host, self.client, self.discovery):
            if obj:
                obj.close()
        self.host = self.client = self.discovery = None
        self.mode = "single"
        self.local_pid = 0
        self.players = [Player(self.player_frames, 0)]
        self.reset()
        self.state = "menu"
        self.message = message

    def start_round(self):
        self.reset()
        self.state = "playing"

    def end_round(self):
        self.state = "gameover"
        me = self.player
        if me and me.score > self.highscore:
            self.highscore = me.score
            self.save_highscore()

    def quit(self):
        self.leave_network()
        pygame.quit()
        sys.exit()

    # --- loop ------------------------------------------------------------------
    def run(self):
        while True:
            dt = min(self.clock.tick(FPS) / 1000.0, 1 / 30)
            self.handle_events()
            self.step(dt)
            self.draw()

    def step(self, dt):
        if self.state == "join" and self.discovery:
            self.found_hosts = self.discovery.hosts()
        if self.mode == "host":
            self.host_receive()
        elif self.mode == "client":
            self.client_tick()

        if self.state == "playing" and self.mode != "client":
            me = self.player
            if me:
                me.dir, me.held = self.read_local_input()
            self.update(dt)

        self.shake = max(0.0, self.shake - dt)
        for pop in self.popups:
            pop.update(dt)
        self.popups = [x for x in self.popups if x.life > 0]

        if self.mode == "host":
            self.host_send()

    # --- input -----------------------------------------------------------------
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.quit()
            if event.type == pygame.KEYDOWN:
                getattr(self, "key_" + self.state)(event)

    def key_menu(self, event):
        k = event.key
        if k in (pygame.K_UP, pygame.K_w):
            self.menu_index = (self.menu_index - 1) % len(MENU)
        elif k in (pygame.K_DOWN, pygame.K_s):
            self.menu_index = (self.menu_index + 1) % len(MENU)
        elif k in (pygame.K_RETURN, pygame.K_SPACE):
            choice = MENU[self.menu_index]
            self.message = ""
            if choice == "Single player":
                self.start_single()
            elif choice == "Host a LAN game":
                self.start_host()
            elif choice == "Join a LAN game":
                self.start_join()
            else:
                self.quit()
        elif k == pygame.K_ESCAPE:
            self.quit()

    def key_join(self, event):
        k = event.key
        if self.typing:
            if k == pygame.K_RETURN and self.ip_text.strip():
                self.connect(self.ip_text.strip())
            elif k == pygame.K_ESCAPE:
                self.typing = False
            elif k == pygame.K_BACKSPACE:
                self.ip_text = self.ip_text[:-1]
            elif event.unicode and event.unicode.lower() in "0123456789.abcdefghijklmnopqrstuvwxyz-" \
                    and len(self.ip_text) < 40:
                self.ip_text += event.unicode
            return
        count = len(self.found_hosts) + 1  # + "type an IP"
        if k in (pygame.K_UP, pygame.K_w):
            self.join_index = (self.join_index - 1) % count
        elif k in (pygame.K_DOWN, pygame.K_s):
            self.join_index = (self.join_index + 1) % count
        elif k in (pygame.K_RETURN, pygame.K_SPACE):
            if self.join_index < len(self.found_hosts):
                self.connect(self.found_hosts[self.join_index]["ip"])
            else:
                self.typing = True
                self.message = ""
        elif k == pygame.K_ESCAPE:
            self.leave_network()

    def key_lobby(self, event):
        if event.key == pygame.K_ESCAPE:
            self.leave_network()
        elif self.mode == "host" and event.key in (pygame.K_SPACE, pygame.K_RETURN):
            self.start_round()

    def key_playing(self, event):
        if event.key == pygame.K_ESCAPE:
            self.leave_network()
        elif event.key in (pygame.K_SPACE, pygame.K_UP, pygame.K_w):
            me = self.player
            if me:
                me.jump_req = True
            self.jumps_sent += 1

    def key_gameover(self, event):
        k = event.key
        if k == pygame.K_ESCAPE:
            self.leave_network()
        elif self.mode != "client" and k in (pygame.K_SPACE, pygame.K_r, pygame.K_RETURN):
            self.start_round()
        elif self.mode == "host" and k == pygame.K_l:
            self.reset()
            self.state = "lobby"

    # --- networking ------------------------------------------------------------
    def host_receive(self):
        joined, left, inputs = self.host.poll()
        for pid in joined:
            if not self.get_player(pid):
                self.players.append(Player(self.player_frames, pid))
            if self.state == "lobby":
                self.reset()  # spread everyone out along the floor
        for pid in left:
            p = self.get_player(pid)
            if p:
                self.players.remove(p)
                if self.state == "lobby":
                    self.reset()
                else:
                    self.add_popup(f"{p.name} left", WIDTH / 2, HEIGHT / 2 - 60, WHITE, "s", 1.5)
        for pid, msg in inputs.items():
            p = self.get_player(pid)
            if not p:
                continue
            try:
                d, j = int(msg.get("d", 0)), int(msg.get("j", 0))
            except (TypeError, ValueError):
                continue
            p.dir = max(-1, min(1, d))
            p.held = bool(msg.get("h"))
            if j > p.jumps_seen:
                p.jump_req = True
                p.jumps_seen = j
        self.host.accepting = self.state == "lobby" and len(self.players) < MAX_PLAYERS
        self.host.beacon(len(self.players))

    def host_send(self):
        self.host.broadcast({
            "t": "s",
            "st": self.state,
            "el": round(self.elapsed, 2),
            "po": [[q.id, q.lane_idx, q.kind, int(q.from_left), round(q.x, 1), round(q.y, 1),
                    round(q.squished, 3), round(q.anim, 2)] for q in self.poos],
            "pl": [[p.pid, round(p.x, 1), round(p.y, 1), round(p.vy), int(p.facing_right), int(p.on_ground),
                    round(p.anim, 2), round(p.invuln, 2), p.score, p.combo, p.best_combo, p.lives,
                    int(p.alive)] for p in self.players],
            "ev": self.events,
        })
        self.events = []

    def client_tick(self):
        c = self.client
        if c.error:
            self.leave_network(c.error)
            return
        if not c.connected:
            self.leave_network("Lost connection to the host.")
            return
        if c.pid is not None:
            self.local_pid = c.pid
        snap, events = c.take()
        if snap:
            self.apply_snapshot(snap)
        for ev in events:
            if ev[0] == "pop":
                _, text, x, y, color, size, life = ev
                self.add_popup(text, x, y, tuple(color), size, life)
            elif ev[0] == "hurt" and ev[1] == self.local_pid:
                self.shake = 0.3
        d, held = self.read_local_input() if self.state == "playing" else (0, False)
        c.send({"t": "in", "d": d, "h": held, "j": self.jumps_sent})

    def apply_snapshot(self, snap):
        prev = self.state
        self.state = snap.get("st", self.state)
        self.elapsed = snap.get("el", 0.0)
        if self.state == "gameover" and prev != "gameover":
            self.end_round()

        old = {q.id: q for q in self.poos}
        poos = []
        for pid, li, kind, from_left, x, y, squished, anim in snap.get("po", []):
            q = old.get(pid)
            if q is None:
                q = Poo(LANES[li], bool(from_left), 0, self.poo_frames[kind], kind)
                q.id = pid
            q.x, q.y, q.squished, q.anim = x, y, squished, anim
            poos.append(q)
        self.poos = poos

        old = {p.pid: p for p in self.players}
        players = []
        for row in snap.get("pl", []):
            pid = row[0]
            p = old.get(pid) or Player(self.player_frames, pid)
            (_, p.x, p.y, p.vy, facing, on_ground, p.anim, p.invuln,
             p.score, p.combo, p.best_combo, p.lives, alive) = row
            p.facing_right, p.on_ground, p.alive = bool(facing), bool(on_ground), bool(alive)
            players.append(p)
        self.players = players

    # --- simulation (single player and host) -----------------------------------
    def update(self, dt):
        before = len(self.unlocked_lanes())
        self.elapsed += dt
        if len(self.unlocked_lanes()) > before:
            self.add_popup("Higher poos incoming!", WIDTH / 2, HEIGHT / 2 - 120, YELLOW, "m", 2.0)
            self.spawn_timer = 0.3  # get the new lane going straight away

        self.spawn_timer -= dt
        if self.spawn_timer <= 0:
            self.spawn_poo()
            self.spawn_timer = self.spawn_interval() * random.uniform(0.6, 1.3)

        if self.elapsed >= LANES[LOW_LANE_COUNT]["unlock"]:
            self.low_timer -= dt
            if self.low_timer <= 0:
                self.spawn_poo(random.choice(LANES[:LOW_LANE_COUNT]))
                self.low_timer = self.low_lane_interval() * random.uniform(0.7, 1.3)

        alive = [p for p in self.players if p.alive]
        for p in alive:
            self.move_player(p, dt)
        for poo in self.poos:
            poo.update(dt)
        for p in alive:
            self.collide_player(p, dt)
        self.poos = [x for x in self.poos if not x.dead]

        if not any(p.alive for p in self.players):
            self.end_round()

    def move_player(self, p, dt):
        if p.jump_req:
            p.jump()
            p.jump_req = False
        p.vx = p.dir * MOVE_SPEED
        if p.dir:
            p.facing_right = p.dir > 0
        p.x = max(0, min(WIDTH - PLAYER_SIZE, p.x + p.vx * dt))

        p.prev_bottom = p.rect().bottom
        p.vy += GRAVITY * dt
        if p.vy < 0 and p.jump_cut and not p.held:  # short hop when jump is released early
            p.vy += GRAVITY * 0.8 * dt
        p.y += p.vy * dt
        p.on_ground = False

    def collide_player(self, p, dt):
        # 1) stomps: every poo we're landing on this frame (falling, feet were above its top
        #    last frame). All of them get squished, so you can land on several at once.
        live = [poo for poo in self.poos if poo.squished <= 0 and not poo.dead]
        prect = p.rect()
        stomped = [poo for poo in live if p.vy > 0 and prect.colliderect(poo.hitbox())
                   and p.prev_bottom <= poo.hitbox().top + 12]
        if stomped:
            # also take any poo just below our feet (within 12px) so near-level poos count as "at once"
            reach = prect.copy()
            reach.height += 12
            stomped += [poo for poo in live if poo not in stomped and reach.colliderect(poo.hitbox())
                        and p.prev_bottom <= poo.hitbox().top + 12]
            # lowest first so the combo climbs as it would landing on them one by one
            for poo in sorted(stomped, key=lambda q: -q.hitbox().top):
                self.stomp(p, poo)
            # stand on the highest one before bouncing
            p.y = min(q.hitbox().top for q in stomped) - PLAYER_SIZE
        # 2) side hits: anything else we're still touching after the bounce
        prect = p.rect()
        if p.invuln <= 0:
            for poo in live:
                if poo not in stomped and prect.colliderect(poo.hitbox()):
                    self.hurt(p)
                    break

        # ground
        if p.y + PLAYER_SIZE >= GROUND_Y:
            p.y = GROUND_Y - PLAYER_SIZE
            if p.vy > 0 and p.combo > 0:
                if p.combo >= 2:
                    self.add_popup(f"Combo x{p.combo} ended", p.x + PLAYER_SIZE / 2, p.y - 30, WHITE, "s")
                p.combo = 0
            p.vy = 0
            p.on_ground = True

        p.update_anim(dt)
        if p.invuln > 0:
            p.invuln -= dt

    def stomp(self, p, poo):
        poo.squished = 0.25
        p.y = poo.hitbox().top - PLAYER_SIZE
        p.vy = BOUNCE_VEL
        p.jump_cut = False  # bounces always go full height
        p.combo += 1
        p.best_combo = max(p.best_combo, p.combo)
        points = poo.lane["points"] * p.combo
        p.score += points
        color = YELLOW if p.combo < 5 else (255, 140, 60) if p.combo < 10 else (255, 80, 200)
        label = f"+{points}" if p.combo == 1 else f"+{points}  x{p.combo}!"
        self.add_popup(label, poo.x + poo.w / 2, poo.y - 30, color)

    def hurt(self, p):
        p.lives -= 1
        p.combo = 0
        p.invuln = INVULN_TIME
        p.vy = -400
        if p.pid == self.local_pid:
            self.shake = 0.3
        if self.mode == "host":
            self.events.append(["hurt", p.pid])
        if p.lives <= 0:
            p.alive = False
            if self.multiplayer:
                self.add_popup(f"{p.name} is out!", p.x + PLAYER_SIZE / 2, p.y - 40, p.color, "m", 1.5)

    # --- drawing ---------------------------------------------------------------
    def draw_text(self, text, font, color, center=None, topleft=None, topright=None):
        s = font.render(text, True, color)
        sh = font.render(text, True, BLACK)
        if center:
            r = s.get_rect(center=center)
        elif topleft:
            r = s.get_rect(topleft=topleft)
        else:
            r = s.get_rect(topright=topright)
        self.frame.blit(sh, r.move(3, 3))
        self.frame.blit(s, r)
        return r

    def draw(self):
        f = self.frame
        f.blit(self.background, (0, 0))
        if self.state in ("lobby", "playing", "gameover"):
            self.draw_world()

        if self.state == "menu":
            self.draw_menu()
        elif self.state == "join":
            self.draw_join()
        elif self.state == "lobby":
            self.draw_lobby()
        else:
            self.draw_hud()
            if self.state == "gameover":
                self.draw_gameover()

        ox = random.randint(-6, 6) if self.shake > 0 else 0
        oy = random.randint(-6, 6) if self.shake > 0 else 0
        self.screen.fill(BLACK)
        self.screen.blit(f, (ox, oy))
        pygame.display.flip()

    def draw_world(self):
        f = self.frame
        for poo in self.poos:
            poo.draw(f)
        for p in self.players:
            if not p.alive:
                continue
            p.draw(f)
            cx = p.x + PLAYER_SIZE / 2
            if p.y + PLAYER_SIZE < 0:  # off the top of the screen: show where he is
                pygame.draw.polygon(f, BLACK, [(cx, 4), (cx - 14, 26), (cx + 14, 26)])
                pygame.draw.polygon(f, p.color, [(cx, 9), (cx - 9, 23), (cx + 9, 23)])
            elif self.multiplayer:
                label = "You" if p.pid == self.local_pid else p.name
                self.draw_text(label, self.font_small, p.color, center=(cx, p.y - 10))
        for pop in self.popups:
            pop.draw(f)

    def dim(self, alpha):
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, alpha))
        self.frame.blit(overlay, (0, 0))

    def draw_menu(self):
        self.dim(110)
        cx = WIDTH / 2
        self.draw_text("POO BOY", self.font_big, (200, 150, 70), center=(cx, 95))
        for i, item in enumerate(MENU):
            selected = i == self.menu_index
            text = f"> {item} <" if selected else item
            self.draw_text(text, self.font_med, YELLOW if selected else WHITE, center=(cx, 190 + i * 46))
        lines = [
            "Bounce on the poos. Don't touch them from the side!",
            "Chain bounces without touching the floor for combos.",
            "Higher poos join in as time goes on, and are worth more points.",
            "Arrows / A D to move  -  Space to jump  -  Esc for menu",
        ]
        for i, line in enumerate(lines):
            self.draw_text(line, self.font_small, WHITE, center=(cx, 385 + i * 30))
        if self.message:
            self.draw_text(self.message, self.font_small, (255, 140, 140), center=(cx, 512))
        # show off the cast
        kinds = ["ground", "high", "fly", "gold"]
        total = sum(POO_TYPES[k]["size"][0] for k in kinds) + 40 * (len(kinds) - 1)
        x = cx - total / 2
        for k in kinds:
            img = self.poo_frames[k][int(pygame.time.get_ticks() / 100) % len(self.poo_frames[k])]
            self.frame.blit(img, (x, 545 + (66 - img.get_height())))
            x += img.get_width() + 40

    def draw_join(self):
        self.dim(140)
        cx = WIDTH / 2
        self.draw_text("JOIN A LAN GAME", self.font_big, (200, 150, 70), center=(cx, 95))
        if self.typing:
            self.draw_text("Type the host's IP address (shown in their lobby):", self.font_small, WHITE,
                           center=(cx, 220))
            box = pygame.Rect(0, 0, 420, 56)
            box.center = (cx, 285)
            pygame.draw.rect(self.frame, (40, 40, 40), box, border_radius=8)
            pygame.draw.rect(self.frame, YELLOW, box, 3, border_radius=8)
            cursor = "_" if (pygame.time.get_ticks() // 400) % 2 else ""
            self.draw_text(self.ip_text + cursor, self.font_med, WHITE, center=box.center)
            self.draw_text("Enter to connect  -  Esc to cancel", self.font_small, WHITE, center=(cx, 350))
        else:
            self.join_index = min(self.join_index, len(self.found_hosts))
            y = 190
            if not self.found_hosts:
                dots = "." * (1 + (pygame.time.get_ticks() // 400) % 3)
                self.draw_text(f"Looking for games on your network{dots}", self.font_small, WHITE, center=(cx, y))
                y += 50
            options = [f"{h.get('name', '?')} ({h['ip']})  -  {h.get('players', '?')}/{h.get('max', '?')} players"
                       + ("" if h.get("open", True) else "  -  in progress") for h in self.found_hosts]
            options.append("Type an IP address")
            for i, text in enumerate(options):
                selected = i == self.join_index
                self.draw_text(f"> {text} <" if selected else text, self.font_med,
                               YELLOW if selected else WHITE, center=(cx, y + i * 48))
            self.draw_text("Up/Down to choose  -  Enter to join  -  Esc to go back", self.font_small, WHITE,
                           center=(cx, 470))
        if self.message:
            self.draw_text(self.message, self.font_small, (255, 140, 140), center=(cx, 520))

    def draw_lobby(self):
        self.dim(110)
        cx = WIDTH / 2
        self.draw_text("LOBBY", self.font_big, (200, 150, 70), center=(cx, 95))
        if self.mode == "host":
            self.draw_text("Others on your network can pick \"Join a LAN game\" to join you.", self.font_small,
                           WHITE, center=(cx, 170))
            self.draw_text(f"If they can't see your game, they can type your IP:  {self.lan_ip}",
                           self.font_small, WHITE, center=(cx, 202))
        elif not self.players:
            self.draw_text("Connecting...", self.font_small, WHITE, center=(cx, 185))
        for i, p in enumerate(self.players):
            you = "  (you)" if p.pid == self.local_pid else ""
            host = "  - host" if p.pid == 0 else ""
            self.draw_text(f"{p.name}{you}{host}", self.font_med, p.color, center=(cx, 260 + i * 44))
        if self.mode == "host":
            prompt = "Press SPACE to start" if len(self.players) > 1 else "Waiting for players...  (SPACE to start anyway)"
            self.draw_text(prompt, self.font_med if len(self.players) > 1 else self.font_small, YELLOW,
                           center=(cx, 450))
        else:
            self.draw_text("Waiting for the host to start...", self.font_small, YELLOW, center=(cx, 450))
        self.draw_text("Esc to leave", self.font_small, WHITE, center=(cx, 490))

    def draw_heart(self, cx, cy, full, r=10):
        def shape(color, dx, dy):
            pts = [(cx + dx - 2 * r, cy + dy), (cx + dx, cy + dy + 2 * r), (cx + dx + 2 * r, cy + dy)]
            pygame.draw.circle(self.frame, color, (cx + dx - r, cy + dy - r // 5), r + 1)
            pygame.draw.circle(self.frame, color, (cx + dx + r, cy + dy - r // 5), r + 1)
            pygame.draw.polygon(self.frame, color, pts)
        shape(BLACK, 2, 2)
        shape(RED if full else (120, 120, 120), 0, 0)

    def draw_hud(self):
        if self.multiplayer:
            y = 10
            for p in self.players:
                name = f"{p.name} (you)" if p.pid == self.local_pid else p.name
                r = self.draw_text(f"{name}: {p.score}", self.font_small, p.color, topleft=(18, y))
                if p.alive:
                    for i in range(START_LIVES):
                        self.draw_heart(r.right + 22 + i * 26, r.centery - 2, i < p.lives, r=6)
                else:
                    self.draw_text("OUT", self.font_small, (170, 170, 170), topleft=(r.right + 14, y))
                y += 34
        else:
            p = self.player
            self.draw_text(f"Score: {p.score}", self.font_med, WHITE, topleft=(18, 10))
            self.draw_text(f"High: {max(self.highscore, p.score)}", self.font_small, WHITE, topleft=(20, 56))
            for i in range(START_LIVES):
                self.draw_heart(WIDTH - 40 - i * 44, 30, i < p.lives)
        lo, hi = self.speed_range()
        self.draw_text(f"Speed: {int((lo + hi) / 2)}", self.font_small, WHITE,
                       topright=(WIDTH - 18, 10 if self.multiplayer else 56))
        me = self.player
        if me and me.combo >= 2:
            pulse = 1 + 0.08 * math.sin(pygame.time.get_ticks() / 80)
            txt = self.font_big.render(f"COMBO x{me.combo}", True, (255, 140, 40))
            txt = pygame.transform.rotozoom(txt, 0, pulse * 0.7)
            self.frame.blit(txt, txt.get_rect(center=(WIDTH / 2, 50)))

    def draw_gameover(self):
        self.dim(150)
        cx = WIDTH / 2
        if self.multiplayer:
            self.draw_text("GAME OVER", self.font_big, (255, 90, 90), center=(cx, 140))
            ranked = sorted(self.players, key=lambda p: -p.score)
            places = ["1st", "2nd", "3rd", "4th"]
            for i, p in enumerate(ranked):
                you = " (you)" if p.pid == self.local_pid else ""
                self.draw_text(f"{places[i]}  {p.name}{you}   {p.score}   best combo x{p.best_combo}",
                               self.font_med, p.color, center=(cx, 225 + i * 46))
            self.draw_text(f"Your high score: {self.highscore}", self.font_small, YELLOW, center=(cx, 430))
            if self.mode == "host":
                hint = "SPACE: play again   -   L: back to lobby   -   Esc: leave"
            else:
                hint = "Waiting for the host to play again   -   Esc: leave"
            self.draw_text(hint, self.font_small, WHITE, center=(cx, 480))
        else:
            p = self.player
            y = HEIGHT / 3
            self.draw_text("GAME OVER", self.font_big, (255, 90, 90), center=(cx, y))
            self.draw_text(f"Score: {p.score}", self.font_med, WHITE, center=(cx, y + 85))
            self.draw_text(f"Best combo: x{p.best_combo}", self.font_med, WHITE, center=(cx, y + 130))
            self.draw_text(f"High score: {self.highscore}", self.font_med, YELLOW, center=(cx, y + 175))
            self.draw_text("SPACE or R to play again  -  Esc for menu", self.font_small, WHITE,
                           center=(cx, y + 240))


if __name__ == "__main__":
    Game().run()
