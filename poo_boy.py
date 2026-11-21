"""Poo Boy - bounce on poos, chain combos, survive the speed-up.

Controls:
    Left / Right (or A / D)   move
    Space / Up / W            jump (hold while landing on a poo for a bigger bounce)
    Esc                       quit
    R / Space                 restart after game over
"""

import math
import os
import random
import sys

import pygame

# --- Paths ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SPRITE_DIR = os.path.join(BASE_DIR, "assets", "Sprites")
BG_PATH = os.path.join(BASE_DIR, "assets", "Background", "background1.png")
HIGHSCORE_FILE = os.path.join(BASE_DIR, "highscore.txt")

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
#   lane 3 (205) -> lane 4 (360, gold)           bounce needs 155
#   floor -> lane 3 needs 205 and lane 2 -> lane 4 needs 245, so neither works
LANES = [
    {"land": 48,  "kind": "ground", "unlock": 0},
    {"land": 115, "kind": "high",   "unlock": 12},
    {"land": 205, "kind": "fly",    "unlock": 24},
    {"land": 360, "kind": "gold",   "unlock": 36},
]
GOLD_MULTIPLIER = 5

START_SPEED_MIN, START_SPEED_MAX = 130.0, 230.0
SPEED_GAIN_PER_SEC = 4.0  # the speed range rises this much every second
SPEED_CAP = 950.0
START_SPAWN_INTERVAL = 1.4
MIN_SPAWN_INTERVAL = 0.45

START_LIVES = 3
INVULN_TIME = 1.5
BASE_POINTS = 10

WHITE = (255, 255, 255)
BLACK = (25, 25, 25)
GOLD = (240, 190, 40)
RED = (220, 50, 60)


# --- Asset loading ----------------------------------------------------------
def load_image(path):
    return pygame.image.load(path).convert_alpha()


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


# --- Entities ---------------------------------------------------------------
class Poo:
    def __init__(self, lane, from_left, speed, frames, kind):
        self.kind = kind
        self.w, self.h = POO_TYPES[kind]["size"]
        self.inset = POO_TYPES[kind]["inset"]
        self.from_left = from_left
        self.vx = speed if from_left else -speed
        self.x = float(-self.w if from_left else WIDTH)
        # place the poo so its stompable top sits at the lane's landing height, never below the floor
        self.y = float(min(GROUND_Y - lane["land"] - self.inset[1], GROUND_Y - self.h))
        self.lane = lane
        # sprites face right; flip the ones walking left
        self.frames = frames if from_left else [pygame.transform.flip(f, True, False) for f in frames]
        self.anim = random.random() * 10
        self.squished = 0.0
        self.dead = False

    @property
    def gold(self):
        return self.kind == "gold"

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
        bob = 0 if self.kind == "ground" else math.sin(self.anim * 6) * 2.5
        if self.gold:
            glow = pygame.Surface((self.w + 30, self.h + 30), pygame.SRCALPHA)
            a = 60 + int(40 * math.sin(self.anim * 8))
            pygame.draw.ellipse(glow, (255, 220, 80, a), glow.get_rect())
            screen.blit(glow, (self.x - 15, self.y - 15 + bob))
        screen.blit(frame, (self.x, self.y + bob))


class Player:
    def __init__(self, frames):
        self.frames_r = frames
        self.frames_l = [pygame.transform.flip(f, True, False) for f in frames]
        self.reset()

    def reset(self):
        self.x = WIDTH / 2 - PLAYER_SIZE / 2
        self.y = float(GROUND_Y - PLAYER_SIZE)
        self.vx = 0.0
        self.vy = 0.0
        self.on_ground = True
        self.facing_right = True
        self.invuln = 0.0
        self.anim = 0.0
        self.jump_cut = False  # True for ground jumps: letting go of jump early gives a short hop

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
        self.player = Player(load_player_frames())
        self.highscore = self.load_highscore()
        self.state = "title"
        self.reset()

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
        self.player.reset()
        self.poos = []
        self.popups = []
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.lives = START_LIVES
        self.elapsed = 0.0
        self.spawn_timer = 0.5
        self.shake = 0.0

    # --- difficulty ------------------------------------------------------------
    def speed_range(self):
        gain = SPEED_GAIN_PER_SEC * self.elapsed
        lo = min(SPEED_CAP * 0.7, START_SPEED_MIN + gain)
        hi = min(SPEED_CAP, START_SPEED_MAX + gain * 1.3)
        return lo, hi

    def spawn_interval(self):
        return max(MIN_SPAWN_INTERVAL, START_SPAWN_INTERVAL - self.elapsed * 0.012)

    def unlocked_lanes(self):
        return [lane for lane in LANES if self.elapsed >= lane["unlock"]]

    def spawn_poo(self):
        lane = random.choice(self.unlocked_lanes())
        from_left = random.random() < 0.5
        # don't spawn on top of a poo that has only just entered the same lane from the same side
        for p in self.poos:
            if p.lane is lane and p.from_left == from_left and (
                (from_left and p.x < p.w * 1.2) or (not from_left and p.x > WIDTH - p.w * 2.2)
            ):
                return
        kind = lane["kind"]
        speed = random.uniform(*self.speed_range())
        self.poos.append(Poo(lane, from_left, speed, self.poo_frames[kind], kind))

    # --- loop ------------------------------------------------------------------
    def run(self):
        while True:
            dt = min(self.clock.tick(FPS) / 1000.0, 1 / 30)
            self.handle_events()
            if self.state == "playing":
                self.update(dt)
            self.draw()

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                pygame.quit()
                sys.exit()
            if event.type == pygame.KEYDOWN:
                if self.state in ("title", "gameover") and event.key in (pygame.K_SPACE, pygame.K_r, pygame.K_RETURN):
                    self.reset()
                    self.state = "playing"
                elif self.state == "playing" and event.key in (pygame.K_SPACE, pygame.K_UP, pygame.K_w):
                    self.player.jump()

    def jump_held(self):
        keys = pygame.key.get_pressed()
        return keys[pygame.K_SPACE] or keys[pygame.K_UP] or keys[pygame.K_w]

    def update(self, dt):
        before = len(self.unlocked_lanes())
        self.elapsed += dt
        if len(self.unlocked_lanes()) > before:
            self.popups.append(Popup("Higher poos incoming!", WIDTH / 2, HEIGHT / 2 - 120, (255, 230, 80),
                                     self.font_med, life=2.0))
            self.spawn_timer = 0.3  # get the new lane going straight away
        p = self.player

        self.spawn_timer -= dt
        if self.spawn_timer <= 0:
            self.spawn_poo()
            self.spawn_timer = self.spawn_interval() * random.uniform(0.6, 1.3)

        # horizontal input
        keys = pygame.key.get_pressed()
        direction = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (keys[pygame.K_LEFT] or keys[pygame.K_a])
        p.vx = direction * MOVE_SPEED
        if direction:
            p.facing_right = direction > 0
        p.x = max(0, min(WIDTH - PLAYER_SIZE, p.x + p.vx * dt))

        # vertical physics
        prev_bottom = p.rect().bottom
        p.vy += GRAVITY * dt
        if p.vy < 0 and p.jump_cut and not self.jump_held():  # short hop when jump is released early
            p.vy += GRAVITY * 0.8 * dt
        p.y += p.vy * dt
        p.on_ground = False

        for poo in self.poos:
            poo.update(dt)
        for poo in self.poos:
            if poo.squished > 0 or poo.dead:
                continue
            hb = poo.hitbox()
            prect = p.rect()
            if not prect.colliderect(hb):
                continue
            # stomp: falling, and our feet were above the poo's top last frame
            if p.vy > 0 and prev_bottom <= hb.top + 12:
                self.stomp(poo)
            elif p.invuln <= 0:
                self.hurt()
                break
        self.poos = [x for x in self.poos if not x.dead]

        # ground
        if p.y + PLAYER_SIZE >= GROUND_Y:
            p.y = GROUND_Y - PLAYER_SIZE
            if p.vy > 0 and self.combo > 0:
                if self.combo >= 2:
                    self.popups.append(Popup(f"Combo x{self.combo} ended", p.x + PLAYER_SIZE / 2, p.y - 30,
                                             WHITE, self.font_small))
                self.combo = 0
            p.vy = 0
            p.on_ground = True

        p.update_anim(dt)
        if p.invuln > 0:
            p.invuln -= dt
        self.shake = max(0.0, self.shake - dt)
        for pop in self.popups:
            pop.update(dt)
        self.popups = [x for x in self.popups if x.life > 0]

    def stomp(self, poo):
        p = self.player
        poo.squished = 0.25
        p.y = poo.hitbox().top - PLAYER_SIZE
        p.vy = BOUNCE_VEL
        p.jump_cut = False  # bounces always go full height
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        points = BASE_POINTS * self.combo * (GOLD_MULTIPLIER if poo.gold else 1)
        self.score += points
        if poo.gold:
            color, label = GOLD, f"GOLD! +{points}"
        else:
            color = (255, 230, 80) if self.combo < 5 else (255, 140, 60) if self.combo < 10 else (255, 80, 200)
            label = f"+{points}" if self.combo == 1 else f"+{points}  x{self.combo}!"
        self.popups.append(Popup(label, poo.x + poo.w / 2, poo.y - 30, color, self.font_med))

    def hurt(self):
        p = self.player
        self.lives -= 1
        self.combo = 0
        self.shake = 0.3
        p.invuln = INVULN_TIME
        p.vy = -400
        if self.lives <= 0:
            self.state = "gameover"
            if self.score > self.highscore:
                self.highscore = self.score
                self.save_highscore()

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

    def draw(self):
        f = self.frame
        f.blit(self.background, (0, 0))
        for poo in self.poos:
            poo.draw(f)
        self.player.draw(f)
        p = self.player
        if p.y + PLAYER_SIZE < 0:  # off the top of the screen: show where he is
            cx = p.x + PLAYER_SIZE / 2
            pygame.draw.polygon(f, BLACK, [(cx, 4), (cx - 14, 26), (cx + 14, 26)])
            pygame.draw.polygon(f, (255, 230, 80), [(cx, 9), (cx - 9, 23), (cx + 9, 23)])
        for pop in self.popups:
            pop.draw(f)

        if self.state != "title":
            self.draw_hud()
        if self.state == "title":
            self.draw_title()
        elif self.state == "gameover":
            self.draw_gameover()

        ox = random.randint(-6, 6) if self.shake > 0 else 0
        oy = random.randint(-6, 6) if self.shake > 0 else 0
        self.screen.fill(BLACK)
        self.screen.blit(f, (ox, oy))
        pygame.display.flip()

    def dim(self, alpha):
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, alpha))
        self.frame.blit(overlay, (0, 0))

    def draw_title(self):
        self.dim(110)
        cx, y = WIDTH / 2, HEIGHT / 4
        self.draw_text("POO BOY", self.font_big, (200, 150, 70), center=(cx, y))
        lines = [
            "Bounce on the poos. Don't touch them from the side!",
            "Chain bounces without touching the floor for combos.",
            "Higher poos join in as time goes on. The golden poo up top is worth 5x.",
            "Arrows / A D to move  -  Space to jump",
        ]
        for i, line in enumerate(lines):
            self.draw_text(line, self.font_small, WHITE, center=(cx, y + 80 + i * 34))
        self.draw_text("Press SPACE to start", self.font_med, (255, 230, 80), center=(cx, y + 250))
        # show off the cast
        kinds = ["ground", "high", "fly", "gold"]
        total = sum(POO_TYPES[k]["size"][0] for k in kinds) + 40 * (len(kinds) - 1)
        x = cx - total / 2
        for k in kinds:
            img = self.poo_frames[k][int(pygame.time.get_ticks() / 100) % len(self.poo_frames[k])]
            self.frame.blit(img, (x, y + 300 + (66 - img.get_height())))
            x += img.get_width() + 40

    def draw_gameover(self):
        self.dim(150)
        cx, y = WIDTH / 2, HEIGHT / 3
        self.draw_text("GAME OVER", self.font_big, (255, 90, 90), center=(cx, y))
        self.draw_text(f"Score: {self.score}", self.font_med, WHITE, center=(cx, y + 85))
        self.draw_text(f"Best combo: x{self.best_combo}", self.font_med, WHITE, center=(cx, y + 130))
        self.draw_text(f"High score: {self.highscore}", self.font_med, (255, 230, 80), center=(cx, y + 175))
        self.draw_text("Press SPACE or R to play again", self.font_small, WHITE, center=(cx, y + 240))

    def draw_heart(self, cx, cy, full, r=10):
        def shape(color, dx, dy, width=0):
            pts = [(cx + dx - 2 * r, cy + dy), (cx + dx, cy + dy + 2 * r), (cx + dx + 2 * r, cy + dy)]
            pygame.draw.circle(self.frame, color, (cx + dx - r, cy + dy - 2), r + 1, width)
            pygame.draw.circle(self.frame, color, (cx + dx + r, cy + dy - 2), r + 1, width)
            pygame.draw.polygon(self.frame, color, pts, width)
        shape(BLACK, 2, 2)
        shape(RED if full else (120, 120, 120), 0, 0)

    def draw_hud(self):
        self.draw_text(f"Score: {self.score}", self.font_med, WHITE, topleft=(18, 10))
        self.draw_text(f"High: {max(self.highscore, self.score)}", self.font_small, WHITE, topleft=(20, 56))
        for i in range(START_LIVES):
            self.draw_heart(WIDTH - 40 - i * 44, 30, i < self.lives)
        lo, hi = self.speed_range()
        self.draw_text(f"Speed: {int((lo + hi) / 2)}", self.font_small, WHITE, topright=(WIDTH - 18, 56))
        if self.combo >= 2:
            pulse = 1 + 0.08 * math.sin(pygame.time.get_ticks() / 80)
            txt = self.font_big.render(f"COMBO x{self.combo}", True, (255, 140, 40))
            txt = pygame.transform.rotozoom(txt, 0, pulse * 0.7)
            self.frame.blit(txt, txt.get_rect(center=(WIDTH / 2, 50)))


if __name__ == "__main__":
    Game().run()
