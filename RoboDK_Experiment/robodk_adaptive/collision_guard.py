"""Collision-aware motion guard: predicts a robot/table collision before it
happens and locally adjusts the path instead of letting the arm drive
through the table.

Why this exists: RoboDK's real-time ``MoveJ``/``MoveL`` never stop a
simulated motion on collision by themselves -- they just mark the colliding
items (visually, and via ``Collisions()``). The ``*_Test`` variants are the
documented way to check a candidate segment for a collision *before*
dispatching it [RoboDK Python API, robodk.com/doc/en/PythonAPI/robodk.html].
This module wraps that pre-check with a bounded local recovery strategy:

    1. Test the direct path. If it is clear, dispatch it -- zero overhead
       in the common case.
    2. If the table (or a zone) is involved in the predicted collision,
       retry through a "lift straight up -> translate at height -> descend"
       detour with progressively higher clearance. The detour never goes
       below the current height.
    3. If a clear detour is found, dispatch it for real and return -- the
       caller simply continues the already-approved plan/action sequence,
       it never needs a brand new plan for this.
    4. If every clearance step still collides (or the collision is with
       something other than the table), raise :class:`TableCollisionError`
       naming the colliding items, so the caller can treat it as a normal,
       recoverable execution failure (see ``robodk_adaptive.executor`` /
       ``experiment``) instead of crashing.

Important: ``MoveJ_Test``/``MoveL_Test`` leave the robot parked at the
collision point. What it hit must be read *before* the robot is restored,
otherwise the query runs at the (collision-free) start pose.

Everything RoboDK-specific is behind the ``Mover`` protocol so the retry
policy itself can be unit-tested with a plain fake -- no RoboDK install or
license required (see ``tests/test_collision_guard.py``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class TableCollisionError(RuntimeError):
    """The arm cannot reach a target without colliding with the table (or
    some other obstacle), even after the configured clearance-escalation
    budget was exhausted.

    This is deliberately a plain, specific exception rather than a crash:
    callers higher up (``Executor``, ``Experiment``) catch it, record it as
    a structured :class:`~robodk_adaptive.models.Failure`, and resume the
    same already-approved plan from fresh, re-observed state instead of
    aborting the whole run.
    """


class Mover(Protocol):
    """The minimal surface the retry policy below needs from a real robot
    connection. Kept free of any RoboDK import so :func:`guarded_joint_move`
    and :func:`guarded_linear_move` can be driven by a fake in tests."""

    def current_joints(self): ...

    def current_pose(self): ...

    def solve_ik(self, pose): ...

    def is_reachable(self, joints) -> bool: ...

    def test_joint_move(self, start_joints, end_joints) -> int: ...

    def test_linear_move(self, start_joints, end_pose) -> int: ...

    def colliding_items(self) -> list[str]:
        """Readable names of the item pairs in collision. Only meaningful
        right after a ``test_*`` call, while the robot is still parked at
        the collision point (before :meth:`restore_joints`)."""
        ...

    def table_collision(self) -> bool:
        """True if the current collision involves the table or a zone, i.e.
        something a higher transit height can clear. Same timing rule as
        :meth:`colliding_items`."""
        ...

    def restore_joints(self, joints) -> None: ...

    def dispatch_joint_move(self, joints) -> None: ...

    def dispatch_linear_move(self, pose) -> None: ...

    def raised_to(self, pose, z_mm: float):
        """Return a copy of ``pose`` lifted to at least ``z_mm`` above the
        table top. A pose already higher is never lowered."""
        ...

    def z_of(self, pose) -> float:
        """Height of ``pose`` above the table top."""
        ...

    def yawed(self, pose, deg: float):
        """Return ``pose`` rotated by ``deg`` about its own vertical tool
        axis, keeping its position."""
        ...


@dataclass
class GuardOutcome:
    """``attempts`` is the number of clearance escalations (or alternate
    approach angles) that were needed; 0 means the originally planned
    direct path was already clear."""

    attempts: int


def _resolved(mover: Mover, pose):
    joints = mover.solve_ik(pose)
    return joints if mover.is_reachable(joints) else None


def _describe(items: Sequence[str], result: int) -> str:
    return ", ".join(items) if items else f"unidentified items (test result={result})"


def guarded_joint_move(
    mover: Mover,
    destination_pose,
    clearance_steps_mm: Sequence[float],
    label: str,
) -> GuardOutcome:
    """Move (joint-interpolated) to ``destination_pose``.

    The direct path is tested first. If RoboDK predicts a collision and the
    table or a zone is involved, this retries through a lift -> translate ->
    descend detour at each height in ``clearance_steps_mm`` (in order, so
    put the smallest, least disruptive clearance first) until one tests
    clear, then dispatches that detour for real. The detour never goes
    below the current height. A collision that does not involve the table
    is not something extra height can fix, so it is reported immediately
    without burning the escalation budget.
    """
    start_joints = mover.current_joints()
    start_pose = mover.current_pose()
    blocked_by = ""

    direct_joints = _resolved(mover, destination_pose)
    if direct_joints is not None:
        result = mover.test_joint_move(start_joints, direct_joints)
        # The robot is parked at the collision point right now; read what
        # it hit before restoring it.
        items = mover.colliding_items() if result != 0 else []
        ground = mover.table_collision() if result != 0 else False
        mover.restore_joints(start_joints)
        if result == 0:
            mover.dispatch_joint_move(direct_joints)
            return GuardOutcome(0)
        blocked_by = _describe(items, result)
        if not ground:
            raise TableCollisionError(
                f"{label}: path is blocked by something other than the "
                f"table (colliding: {blocked_by})"
            )

    for attempt, clearance in enumerate(clearance_steps_mm, start=1):
        lift_joints = _resolved(mover, mover.raised_to(start_pose, clearance))
        over_joints = _resolved(
            mover,
            mover.raised_to(
                destination_pose, max(clearance, mover.z_of(start_pose))
            ),
        )
        final_joints = _resolved(mover, destination_pose)
        if lift_joints is None or over_joints is None or final_joints is None:
            continue
        legs = ((start_joints, lift_joints), (lift_joints, over_joints), (over_joints, final_joints))
        clear = all(mover.test_joint_move(a, b) == 0 for a, b in legs)
        mover.restore_joints(start_joints)
        if clear:
            mover.dispatch_joint_move(lift_joints)
            mover.dispatch_joint_move(over_joints)
            mover.dispatch_joint_move(final_joints)
            return GuardOutcome(attempt)

    raise TableCollisionError(
        f"{label}: the arm cannot clear the table even after raising "
        f"transit height through {list(clearance_steps_mm)} mm "
        f"(colliding: {blocked_by or 'unreachable detour'}); stopping "
        f"before a collision occurs"
    )


def guarded_linear_move(
    mover: Mover,
    destination_pose,
    label: str,
    yaw_alternatives_deg: Sequence[float] = (),
) -> GuardOutcome:
    """Dispatch a linear (``MoveL``) segment, after confirming the straight
    path is collision-free.

    A linear descent (e.g. the final approach onto a pick point) has a
    fixed XY, so there is no safe "go up and over" detour. If the straight
    descent is blocked, each angle in ``yaw_alternatives_deg`` is tried: the
    arm re-approaches the same XY from the current height with the tool
    turned about its vertical axis (a square part is grasped the same way),
    which changes which links sweep through the workspace. Only a path that
    tests clear is dispatched. If none does, a :class:`TableCollisionError`
    naming the colliding items is raised for the caller's recovery.
    """
    start_joints = mover.current_joints()
    start_pose = mover.current_pose()
    result = mover.test_linear_move(start_joints, destination_pose)
    items = mover.colliding_items() if result > 0 else []
    mover.restore_joints(start_joints)
    if result == 0:
        mover.dispatch_linear_move(destination_pose)
        return GuardOutcome(0)
    if result < 0:
        raise RuntimeError(
            f"{label}: linear move is not feasible (collision test result={result})"
        )

    blocked_by = _describe(items, result)
    start_height = mover.z_of(start_pose)
    for attempt, deg in enumerate(yaw_alternatives_deg, start=1):
        yawed_destination = mover.yawed(destination_pose, deg)
        yawed_approach = mover.raised_to(yawed_destination, start_height)
        approach_joints = _resolved(mover, yawed_approach)
        if approach_joints is None or _resolved(mover, yawed_destination) is None:
            continue
        clear = mover.test_joint_move(start_joints, approach_joints) == 0
        if clear:
            clear = mover.test_linear_move(approach_joints, yawed_destination) == 0
        mover.restore_joints(start_joints)
        if clear:
            mover.dispatch_joint_move(approach_joints)
            mover.dispatch_linear_move(yawed_destination)
            return GuardOutcome(attempt)

    raise TableCollisionError(
        f"{label}: the linear descent would collide (colliding: "
        f"{blocked_by}) and no alternate approach angle "
        f"{list(yaw_alternatives_deg)} deg is clear; stopping before a "
        f"collision occurs"
    )


def probe_descent(mover: Mover, approach_pose, pick_pose) -> list[str]:
    """Dry-run a joint move to ``approach_pose`` followed by a linear descent
    to ``pick_pose`` without dispatching anything. Returns ``[]`` when the
    whole path tests clear, otherwise the names of what it would hit. The
    robot is always returned to where it started."""
    start_joints = mover.current_joints()
    try:
        approach_joints = _resolved(mover, approach_pose)
        if approach_joints is None or _resolved(mover, pick_pose) is None:
            return ["unreachable pose"]
        result = mover.test_joint_move(start_joints, approach_joints)
        if result != 0:
            return mover.colliding_items() or [f"unidentified (approach result={result})"]
        result = mover.test_linear_move(approach_joints, pick_pose)
        if result != 0:
            return mover.colliding_items() or [f"unidentified (descent result={result})"]
        return []
    finally:
        mover.restore_joints(start_joints)
