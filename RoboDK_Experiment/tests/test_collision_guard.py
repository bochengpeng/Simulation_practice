from RoboDK_Experiment.robodk_adaptive.collision_guard import (
    TableCollisionError,
    guarded_joint_move,
    guarded_linear_move,
    probe_descent,
)


class FakePose:
    """Minimal stand-in for a robodk.robomath.Mat: only what the guard
    needs (xyz access/copy and equality for readable asserts)."""

    def __init__(self, x, y, z, yaw=0):
        self.x, self.y, self.z, self.yaw = x, y, z, yaw

    def copy(self):
        return FakePose(self.x, self.y, self.z, self.yaw)

    def __eq__(self, other):
        return (self.x, self.y, self.z, self.yaw) == (other.x, other.y, other.z, other.yaw)

    def __repr__(self):
        return f"FakePose({self.x},{self.y},{self.z},yaw={self.yaw})"


class FakeJoints(tuple):
    """Opaque joints token; the guard never inspects its contents, only
    passes it between solve_ik/test/dispatch, so a plain (x,y,z) tuple
    standing in for the pose it was solved from is enough to assert on."""


class FakeMover:
    """A plain-Python Mover used to unit-test collision_guard's retry
    policy without any RoboDK dependency.

    Models: a move that changes XY while either end is below
    ``blocked_until_clearance`` sweeps low over the table and is blocked; a
    pure vertical move (same XY) or a horizontal move flown entirely at or
    above that height is always clear -- which is exactly why the
    lift/translate/descend detour works. ``unreachable_z_values`` marks
    specific heights SolveIK can't reach, independent of collision.
    ``linear_blocked``/``blocked_yaws``/``non_table_block`` drive the
    simpler linear-move and non-table-collision scenarios directly.

    Like RoboDK's ``*_Test`` calls, a blocked test leaves the robot "parked
    at the collision point": ``colliding_items``/``table_collision`` only
    report a collision until ``restore_joints`` is called.
    """

    def __init__(
        self,
        start=(0, 0, 300),
        blocked_until_clearance=None,
        non_table_block=False,
        unreachable_z_values=frozenset(),
        linear_blocked=False,
        blocked_yaws=frozenset(),
    ):
        self._pose = FakePose(*start)
        self.blocked_until_clearance = blocked_until_clearance
        self.non_table_block = non_table_block
        self.unreachable_z_values = unreachable_z_values
        self.linear_blocked = linear_blocked
        self.blocked_yaws = blocked_yaws
        self.dispatched = []
        self.restores = 0
        self._in_collision = False

    def current_joints(self):
        return FakeJoints((self._pose.x, self._pose.y, self._pose.z))

    def current_pose(self):
        return self._pose.copy()

    def solve_ik(self, pose):
        if round(pose.z, 3) in self.unreachable_z_values:
            return None
        return FakeJoints((pose.x, pose.y, pose.z))

    def is_reachable(self, joints) -> bool:
        return joints is not None

    def _blocked(self, start_joints, end_joints) -> bool:
        if self.non_table_block:
            return True
        if self.blocked_until_clearance is None:
            return False
        same_xy = (start_joints[0], start_joints[1]) == (end_joints[0], end_joints[1])
        if same_xy:
            return False
        return min(start_joints[2], end_joints[2]) < self.blocked_until_clearance

    def test_joint_move(self, start_joints, end_joints) -> int:
        blocked = self._blocked(start_joints, end_joints)
        self._in_collision = blocked
        return 1 if blocked else 0

    def test_linear_move(self, start_joints, end_pose) -> int:
        blocked = (
            self.non_table_block
            or self.linear_blocked
            or end_pose.yaw in self.blocked_yaws
        )
        self._in_collision = blocked
        return 1 if blocked else 0

    def colliding_items(self):
        if not self._in_collision:
            return []
        return ["Part_B<->Robot[3]"] if self.non_table_block else ["SSM3_Zone_B<->Robot[3]"]

    def table_collision(self) -> bool:
        return self._in_collision and not self.non_table_block

    def restore_joints(self, joints) -> None:
        self.restores += 1
        self._in_collision = False
        self._pose = FakePose(*joints)

    def dispatch_joint_move(self, joints) -> None:
        self.dispatched.append(("joint", joints))
        self._pose = FakePose(*joints)

    def dispatch_linear_move(self, pose) -> None:
        self.dispatched.append(("linear", pose))
        self._pose = pose.copy()

    def raised_to(self, pose, z_mm: float):
        result = pose.copy()
        result.z = max(result.z, z_mm)
        return result

    def z_of(self, pose) -> float:
        return pose.z

    def yawed(self, pose, deg: float):
        result = pose.copy()
        result.yaw = pose.yaw + deg
        return result


def test_clear_direct_path_dispatches_without_adjustment():
    mover = FakeMover(start=(0, 0, 300))
    outcome = guarded_joint_move(mover, FakePose(500, 0, 230), (60, 140, 240), "PickApproach")
    assert outcome.attempts == 0
    assert mover.dispatched == [("joint", FakeJoints((500, 0, 230)))]


def test_table_collision_is_resolved_by_raising_clearance():
    # Starting low (e.g. just after a pick) and heading to a different XY:
    # the direct diagonal sweep is blocked below 240mm of clearance, so
    # only the last of the three escalation steps clears it.
    mover = FakeMover(start=(0, 0, 100), blocked_until_clearance=240)
    outcome = guarded_joint_move(mover, FakePose(500, 0, 230), (60, 140, 240), "PickApproach")
    assert outcome.attempts == 3
    assert [kind for kind, _ in mover.dispatched] == ["joint", "joint", "joint"]
    assert mover.dispatched[0][1] == FakeJoints((0, 0, 240))     # lift straight up
    assert mover.dispatched[1][1] == FakeJoints((500, 0, 240))   # translate at height
    assert mover.dispatched[2][1] == FakeJoints((500, 0, 230))   # descend to the real target


def test_detour_never_descends_below_the_start_height():
    # Starting at the 350 safe height, a 60mm "clearance" detour must not
    # drop the arm to 60mm: the lift stays at 350 and only the final leg
    # descends vertically to the real target.
    mover = FakeMover(start=(0, 0, 350), blocked_until_clearance=240)
    outcome = guarded_joint_move(mover, FakePose(500, 0, 230), (60, 140, 240), "PickApproach")
    assert outcome.attempts == 1
    heights = [joints[2] for _, joints in mover.dispatched]
    assert heights == [350, 350, 230]


def test_non_table_collision_raises_immediately_without_escalation():
    mover = FakeMover(start=(0, 0, 300), non_table_block=True)
    try:
        guarded_joint_move(mover, FakePose(500, 0, 230), (60, 140, 240), "PickApproach")
        assert False, "expected TableCollisionError"
    except TableCollisionError as exc:
        assert "Part_B<->Robot[3]" in str(exc)
    assert mover.dispatched == []  # never tries to escalate clearance for a non-table block


def test_escalation_budget_exhausted_raises_table_collision_error():
    # Nothing in the configured clearance steps ever clears.
    mover = FakeMover(start=(0, 0, 100), blocked_until_clearance=10_000)
    try:
        guarded_joint_move(mover, FakePose(500, 0, 230), (60, 140, 240), "PickApproach")
        assert False, "expected TableCollisionError"
    except TableCollisionError as exc:
        # What was hit is read while the robot is still parked at the
        # collision point, so the named items survive the restore.
        assert "SSM3_Zone_B<->Robot[3]" in str(exc)
    assert mover.dispatched == []


def test_unreachable_clearance_step_is_skipped_not_fatal():
    # 140mm clearance is unreachable at this pose and must be skipped
    # (not treated as fatal); 60mm is reachable but still too low to
    # clear, so the detour only succeeds once it reaches 240mm.
    mover = FakeMover(
        start=(0, 0, 100),
        blocked_until_clearance=240,
        unreachable_z_values={140},
    )
    outcome = guarded_joint_move(mover, FakePose(500, 0, 230), (60, 140, 240), "PickApproach")
    assert outcome.attempts == 3
    assert mover.dispatched[-1][1] == FakeJoints((500, 0, 230))


def test_guarded_linear_move_clear_path_dispatches_directly():
    mover = FakeMover(start=(500, 0, 230))
    outcome = guarded_linear_move(mover, FakePose(500, 0, 90), "Pick")
    assert outcome.attempts == 0
    assert mover.dispatched == [("linear", FakePose(500, 0, 90))]


def test_guarded_linear_move_blocked_without_alternatives_raises_with_items():
    mover = FakeMover(start=(500, 0, 230), linear_blocked=True)
    try:
        guarded_linear_move(mover, FakePose(500, 0, 90), "Pick")
        assert False, "expected TableCollisionError"
    except TableCollisionError as exc:
        assert "SSM3_Zone_B<->Robot[3]" in str(exc)
    assert mover.dispatched == []


def test_guarded_linear_move_uses_alternate_yaw_when_straight_descent_blocked():
    mover = FakeMover(start=(500, 0, 350), blocked_yaws={0, 90})
    outcome = guarded_linear_move(
        mover, FakePose(500, 0, 90), "Pick", yaw_alternatives_deg=(90, -90, 180)
    )
    assert outcome.attempts == 2  # 90 is still blocked, -90 is clear
    assert mover.dispatched == [
        ("joint", FakeJoints((500, 0, 350))),
        ("linear", FakePose(500, 0, 90, yaw=-90)),
    ]


def test_guarded_linear_move_raises_when_every_yaw_is_blocked():
    mover = FakeMover(start=(500, 0, 350), blocked_yaws={0, 90, -90, 180})
    try:
        guarded_linear_move(
            mover, FakePose(500, 0, 90), "Pick", yaw_alternatives_deg=(90, -90, 180)
        )
        assert False, "expected TableCollisionError"
    except TableCollisionError as exc:
        assert "SSM3_Zone_B<->Robot[3]" in str(exc)
    assert mover.dispatched == []
    assert mover.current_joints() == FakeJoints((500, 0, 350))  # left where it started


def test_probe_descent_clear_path_returns_empty_and_restores_robot():
    mover = FakeMover(start=(0, 0, 350))
    items = probe_descent(mover, FakePose(500, 0, 350), FakePose(500, 0, 90))
    assert items == []
    assert mover.dispatched == []
    assert mover.current_joints() == FakeJoints((0, 0, 350))


def test_probe_descent_blocked_path_names_items_and_restores_robot():
    mover = FakeMover(start=(0, 0, 350), linear_blocked=True)
    items = probe_descent(mover, FakePose(500, 0, 350), FakePose(500, 0, 90))
    assert items == ["SSM3_Zone_B<->Robot[3]"]
    assert mover.dispatched == []
    assert mover.current_joints() == FakeJoints((0, 0, 350))
