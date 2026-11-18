from clipper.facecam import cam_rect_from_face, choose_face


def test_static_corner_face_wins_over_moving_game_faces():
    cam = (0.02, 0.06, 0.05, 0.09)
    frames = []
    for i in range(10):
        boxes = [(0.4 + 0.03 * i, 0.3, 0.12, 0.2)]           # spelansikte som rör sig i mitten
        if i % 3 != 0:                                        # streamern syns i 7 av 10
            boxes.append((cam[0] + 0.004 * (i % 2), cam[1], cam[2], cam[3]))
        frames.append(boxes)
    det = choose_face(frames)
    assert det and det.support >= 6 and det.box[0] < 0.05


def test_no_facecam_when_faces_only_in_center_or_too_rare():
    assert choose_face([[(0.45, 0.4, 0.1, 0.18)]] * 8) is None             # mitten = spelet
    assert choose_face([[(0.02, 0.05, 0.05, 0.09)]] + [[]] * 9) is None      # bara 1 av 10


def test_cam_rect_has_target_aspect_and_stays_inside():
    r = cam_rect_from_face((0.01, 0.06, 0.05, 0.09), 16 / 9, 1080 / 700)
    assert r.x >= 0 and r.y >= 0 and r.x + r.w <= 1 and r.y + r.h <= 1
    assert abs((r.w * 16 / 9) / r.h - 1080 / 700) < 0.02
