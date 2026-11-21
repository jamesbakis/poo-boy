# poo-boy

Sprites Created by Jacob Bell
## Play

```
pip install pygame-ce
python poo_boy.py
```

- **Left/Right** or **A/D** to move, **Space/Up/W** to jump
- Land on a poo to bounce off it. Chaining bounces without touching the ground builds a combo, and each bounce is worth the poo's points x combo
- Bouncing off a poo goes exactly as high as a full jump
- Touching a poo from the side costs a life (you have 3)
- Poos come in from both sides in 4 fixed lanes, and they get faster over time
- Only the floor lane runs at first. A higher lane joins every 12 seconds
- You can jump from the floor to lanes 1 and 2. Lane 3 needs a bounce off lane 1 or 2, and lane 4 needs a bounce off lane 3
- Poo points by lane: floor 100, 2nd 130, 3rd 170, gold (top) 300

## Multiplayer (same network)

Up to 4 players can play on the same Wi-Fi or wired network. Everyone runs `python poo_boy.py`:

1. One player picks **Host a LAN game**. Their lobby shows their IP address.
2. Everyone else picks **Join a LAN game**. The host's game should show up in the list. If it doesn't, choose **Type an IP address** and enter the IP shown in the host's lobby.
3. The host presses **Space** to start.

Everyone shares the same poos, and each player has their own score, combo and lives. When everyone is out, the highest score wins. The host can press **Space** to play again or **L** to go back to the lobby so more people can join.

The game uses TCP port 50555 and UDP port 50556. The first time you host, Windows may ask whether to allow Python through the firewall. Allow it on private networks, or other players won't be able to connect.
