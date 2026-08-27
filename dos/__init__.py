"""dos — Domain OS kernel for agents.

A domain operating system sits between agent runtimes and the real business
world.  Its three invariants, borrowed from classical operating systems:

1. The kernel never trusts user space (agents).  Every access goes through
   the syscall boundary and is capability-checked and audited.
2. There is exactly one trusted copy of world state, maintained by the
   kernel.  Agents only ever see snapshots.
3. Sending a command is not changing reality.  Downlink commands open
   transactions that are later confirmed (or refuted / timed out) by
   uplink telemetry — the fsck role.

Kernel structure (each module is an OS organ, not a dataflow role):

- ``journal``     append-only log of immutable facts (WAL)
- ``namespace``   the mounted world, a hierarchical path space with
                  page-cache-like derived views and inotify-like watches
- ``devices``     device drivers: normalize interrupts, dispatch commands,
                  verify pending transactions
- ``capabilities`` unforgeable tokens; the only currency of authority
- ``consistency`` fsck/watchdog over pending transactions
- ``process``     agent business processes as supervised user processes
                  (systemd-flavoured units) woken by a scheduler
- ``kernel``      ties everything together; exposes the syscall surface
"""

from .journal import Journal, Record
from .namespace import Namespace, Snapshot, NotFound
from .capabilities import Capability, CapabilityError
from .devices import Driver
from .process import ProcessSpec, ProcessState
from .kernel import Kernel, ActResult, FrozenPathError, PreconditionError

__all__ = [
    "Journal",
    "Record",
    "Namespace",
    "Snapshot",
    "NotFound",
    "Capability",
    "CapabilityError",
    "Driver",
    "ProcessSpec",
    "ProcessState",
    "Kernel",
    "ActResult",
    "FrozenPathError",
    "PreconditionError",
]
