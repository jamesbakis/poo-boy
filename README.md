# poo-boy

Sprites Created by Jacob Bell
## Play

```
pip install pygame-ce
python poo_boy.py
```

- **Left/Right** or **A/D** to move, **Space/Up/W** to jump
- Land on a poo to bounce off it. Chaining bounces without touching the ground builds a combo, and each bounce is worth `10 x combo`
- Bouncing off a poo goes exactly as high as a full jump
- Touching a poo from the side costs a life (you have 3)
- Poos come in from both sides in 4 fixed lanes, and they get faster over time
- Only the floor lane runs at first. A higher lane joins every 12 seconds
- You can jump from the floor to lanes 1 and 2. Lane 3 needs a bounce off lane 1 or 2, and lane 4 needs a bounce off lane 3
- The top lane is the golden poo, worth 5x
