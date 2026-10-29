from __future__ import annotations

from se_mcp import surface


def flat_map(size: int = 30, h: float = 1.0) -> surface.HeightMap:
    heights = [[h for _ in range(size)] for _ in range(size)]
    return surface.HeightMap([0, 0, 0], [0, 0, 1], [1, 0, 0], [0, 1, 0], -size // 2, -size // 2, 1.0, heights, {})


def test_flat_deck_has_a_spot_and_render_marks_it():
    hm = flat_map()
    spots = surface.find_spots(hm, (-2, 2, -2, 2), [(1, 0)], (0, 0))
    assert spots and spots[0]["a"] == 0 and spots[0]["b"] == 0
    assert "@" in surface.render(hm, (0, 0))


def test_hazards_and_tall_blocks_rule_spots_out():
    hm = flat_map()
    hm.hazards[(15, 15)] = "T"  # a thruster at (0, 0)
    spots = surface.find_spots(hm, (-2, 2, -2, 2), [], (0, 0))
    assert all(abs(s["a"]) > 4 or abs(s["b"]) > 4 for s in spots)  # footprint + 2 m buffer keeps clear
    assert "T" in surface.render(hm)
    tall = flat_map()
    for i in range(30):
        tall.heights[i][15] = 6.0  # a wall along v = 0
    assert all(abs(s["b"]) >= 3 for s in surface.find_spots(tall, (-2, 2, -2, 2), [], (0, 0)))


def test_slopes_are_not_level():
    hm = flat_map()
    for i in range(30):
        for j in range(30):
            hm.heights[i][j] = 1.0 + 0.2 * j  # a 1:5 armor slope
    assert surface.find_spots(hm, (-1, 1, -1, 1), [], (0, 0)) == []


def test_route_clearance_sees_the_tower_on_the_way():
    hm = flat_map()
    hm.heights[15][15] = 11.0
    assert surface.route_clearance(hm, (-1, 1, -1, 1), (-10, 0), (10, 0)) == 11.0
