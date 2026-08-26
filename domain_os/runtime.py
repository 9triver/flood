"""In-process reference runtime for an agent-oriented domain operating system."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from .contracts import DomainPolicy, DomainStore, InfrastructureDriver
from .models import (
    Capability,
    Command,
    CommandState,
    DerivedProduct,
    DomainEvent,
    Intent,
    Observation,
    ObservationQuality,
    ProjectedValue,
    Resource,
    new_id,
    utc_now,
)


EventHandler = Callable[[DomainEvent], Any]


class DomainRuntimeError(RuntimeError):
    pass


class DomainRuntime:
    """Owns the live domain state between agents and infrastructure drivers."""

    def __init__(
        self,
        *,
        domain_id: str,
        policy: DomainPolicy,
        store: DomainStore | None = None,
    ) -> None:
        self.domain_id = str(domain_id or "").strip()
        if not self.domain_id:
            raise ValueError("domain_id must not be empty")
        self.policy = policy
        if store is not None and not isinstance(store, DomainStore):
            raise TypeError("store must implement DomainStore")
        self.store = store
        self._drivers: dict[str, InfrastructureDriver] = {}
        self._capabilities: dict[str, Capability] = {}
        self._resources: dict[str, Resource] = {}
        self._observations: list[Observation] = []
        self._observation_ids: set[str] = set()
        self._projections: dict[str, dict[str, ProjectedValue]] = {}
        self._projection_epochs: dict[str, str] = {}
        self._projection_epoch_history: dict[str, set[str]] = {}
        self._commands: dict[str, Command] = {}
        self._products: dict[str, DerivedProduct] = {}
        self._events: list[DomainEvent] = []
        self._subscribers: list[tuple[str | None, EventHandler]] = []
        self._started = False
        self._restored = False

    @property
    def started(self) -> bool:
        return self._started

    def register_driver(self, driver: InfrastructureDriver) -> None:
        if self._started:
            raise DomainRuntimeError("drivers cannot be registered after start")
        if not isinstance(driver, InfrastructureDriver):
            raise TypeError("driver must implement InfrastructureDriver")
        if driver.driver_id in self._drivers:
            raise DomainRuntimeError(f"duplicate driver: {driver.driver_id}")
        self._drivers[driver.driver_id] = driver

    def register_capability(self, capability: Capability) -> None:
        if capability.capability_id in self._capabilities:
            raise DomainRuntimeError(
                f"duplicate capability: {capability.capability_id}"
            )
        self._capabilities[capability.capability_id] = capability

    def register_resource(self, resource: Resource) -> None:
        if resource.resource_id in self._resources:
            raise DomainRuntimeError(f"duplicate resource: {resource.resource_id}")
        if resource.driver_id not in self._drivers:
            raise DomainRuntimeError(
                f"resource driver is not registered: {resource.driver_id}"
            )
        missing = resource.capabilities - self._capabilities.keys()
        if missing:
            raise DomainRuntimeError(
                f"resource references unknown capabilities: {', '.join(sorted(missing))}"
            )
        self._resources[resource.resource_id] = resource

    async def start(self) -> None:
        if self._started:
            return
        recovered_commands: tuple[Command, ...] = ()
        if not self._restored:
            recovered_commands = self._restore()
            self._restored = True
        started: list[InfrastructureDriver] = []
        self._started = True
        try:
            for command in recovered_commands:
                await self._command_event(command)
            for resource_id in self._projections:
                await self._reconcile(resource_id)
            for driver in self._drivers.values():
                async def sink(
                    observation: Observation,
                    *,
                    driver_id: str = driver.driver_id,
                ) -> None:
                    await self.ingest(observation, driver_id=driver_id)

                started.append(driver)
                await driver.start(sink)
        except BaseException:
            for driver in reversed(started):
                try:
                    await driver.stop()
                except BaseException:
                    pass
            self._started = False
            raise

    async def stop(self) -> None:
        if not self._started:
            return
        errors: list[BaseException] = []
        for driver in reversed(tuple(self._drivers.values())):
            try:
                await driver.stop()
            except BaseException as exc:
                errors.append(exc)
        self._started = False
        if errors:
            raise DomainRuntimeError("; ".join(str(error) for error in errors))

    def resources(self, *, resource_type: str | None = None) -> tuple[Resource, ...]:
        values = tuple(self._resources.values())
        if resource_type is not None:
            values = tuple(
                item for item in values if item.resource_type == resource_type
            )
        return values

    def resource(self, resource_id: str) -> Resource:
        try:
            return self._resources[resource_id]
        except KeyError as exc:
            raise DomainRuntimeError(f"unknown resource: {resource_id}") from exc

    def observations(self, *, resource_id: str | None = None) -> tuple[Observation, ...]:
        values = tuple(self._observations)
        if resource_id is not None:
            values = tuple(item for item in values if item.resource_id == resource_id)
        return values

    def projection(self, resource_id: str) -> Mapping[str, ProjectedValue]:
        self.resource(resource_id)
        return dict(self._projections.get(resource_id, {}))

    def events(self, *, event_type: str | None = None) -> tuple[DomainEvent, ...]:
        values = tuple(self._events)
        if event_type is not None:
            values = tuple(item for item in values if item.event_type == event_type)
        return values

    def commands(self) -> tuple[Command, ...]:
        return tuple(self._commands.values())

    def command(self, command_id: str) -> Command:
        try:
            return self._commands[command_id]
        except KeyError as exc:
            raise DomainRuntimeError(f"unknown command: {command_id}") from exc

    def products(
        self,
        *,
        product_type: str | None = None,
        subject_id: str | None = None,
    ) -> tuple[DerivedProduct, ...]:
        values = tuple(self._products.values())
        if product_type is not None:
            values = tuple(
                item for item in values if item.product_type == product_type
            )
        if subject_id is not None:
            values = tuple(item for item in values if item.subject_id == subject_id)
        return values

    def product(self, product_id: str) -> DerivedProduct:
        try:
            return self._products[product_id]
        except KeyError as exc:
            raise DomainRuntimeError(f"unknown product: {product_id}") from exc

    def subscribe(
        self,
        handler: EventHandler,
        *,
        event_type: str | None = None,
    ) -> Callable[[], None]:
        entry = (event_type, handler)
        self._subscribers.append(entry)
        active = True

        def dispose() -> None:
            nonlocal active
            if not active:
                return
            active = False
            try:
                self._subscribers.remove(entry)
            except ValueError:
                pass

        return dispose

    async def ingest(self, observation: Observation, *, driver_id: str) -> None:
        if not self._started:
            raise DomainRuntimeError("domain runtime is not started")
        resource = self.resource(observation.resource_id)
        if resource.driver_id != driver_id:
            raise DomainRuntimeError(
                f"driver {driver_id} cannot report resource {resource.resource_id}"
            )
        if observation.observation_id in self._observation_ids:
            return
        if self.store is not None:
            self.store.append_observation(self.domain_id, observation)
        self._observation_ids.add(observation.observation_id)
        self._observations.append(observation)
        await self._emit(
            "domain.observation.recorded",
            observation.resource_id,
            {
                "observation_id": observation.observation_id,
                "metric": observation.metric,
                "quality": observation.quality.value,
            },
            causation_id=observation.observation_id,
        )

        if not self._apply_projection(observation):
            return
        await self._emit(
            "domain.projection.updated",
            observation.resource_id,
            {
                "metric": observation.metric,
                "value": observation.value,
                "unit": observation.unit,
                "observation_id": observation.observation_id,
            },
            causation_id=observation.observation_id,
        )
        await self._reconcile(observation.resource_id)

    async def record_product(self, product: DerivedProduct) -> DerivedProduct:
        if not self._started:
            raise DomainRuntimeError("domain runtime is not started")
        current = self._products.get(product.product_id)
        if current is not None:
            if current == product:
                return current
            raise DomainRuntimeError(
                f"product id already exists with different content: {product.product_id}"
            )
        if self.store is not None:
            self.store.append_product(self.domain_id, product)
        self._products[product.product_id] = product
        await self._emit(
            "domain.product.recorded",
            product.subject_id,
            {
                "product_id": product.product_id,
                "product_type": product.product_type,
                "producer_id": product.producer_id,
                "input_refs": list(product.input_refs),
                "valid_from": (
                    product.valid_from.isoformat()
                    if product.valid_from is not None
                    else None
                ),
                "valid_to": (
                    product.valid_to.isoformat()
                    if product.valid_to is not None
                    else None
                ),
                "artifact_names": sorted(product.artifacts),
            },
            correlation_id=product.correlation_id,
            causation_id=product.causation_id or product.product_id,
        )
        return product

    async def publish_event(
        self,
        event_type: str,
        subject_id: str,
        data: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> DomainEvent:
        if not self._started:
            raise DomainRuntimeError("domain runtime is not started")
        return await self._emit(
            event_type,
            subject_id,
            data,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    async def submit_intent(self, intent: Intent) -> Command:
        existing = next(
            (
                command for command in self._commands.values()
                if command.intent.intent_id == intent.intent_id
            ),
            None,
        )
        if existing is not None:
            if existing.intent == intent:
                return existing
            raise DomainRuntimeError(
                f"intent id already exists with different content: {intent.intent_id}"
            )
        resource = self.resource(intent.resource_id)
        if intent.capability_id not in resource.capabilities:
            raise DomainRuntimeError(
                f"resource {resource.resource_id} does not expose "
                f"{intent.capability_id}"
            )
        capability = self._capabilities[intent.capability_id]
        decision = self.policy.evaluate(intent, resource, capability)
        now = utc_now()
        if not decision.allowed:
            state = CommandState.REJECTED
        elif decision.requires_approval:
            state = CommandState.PENDING_APPROVAL
        else:
            state = CommandState.DISPATCHING
        command = Command(
            command_id=new_id("command"),
            intent=intent,
            driver_id=resource.driver_id,
            state=state,
            created_at=now,
            updated_at=now,
            policy_reason=decision.reason,
        )
        self._save_command(command)
        if state is CommandState.DISPATCHING:
            return await self._dispatch(command)
        await self._emit(
            f"domain.command.{command.state.value}",
            resource.resource_id,
            {
                "command_id": command.command_id,
                "intent_id": intent.intent_id,
                "actor_id": intent.actor_id,
                "capability_id": intent.capability_id,
                "reason": decision.reason,
            },
            correlation_id=intent.correlation_id,
            causation_id=intent.intent_id,
        )
        return command

    async def approve(self, command_id: str, *, approver_id: str) -> Command:
        command = self.command(command_id)
        if command.state is not CommandState.PENDING_APPROVAL:
            raise DomainRuntimeError(
                f"command is not pending approval: {command.command_id}"
            )
        approver = str(approver_id or "").strip()
        if not approver:
            raise ValueError("approver_id must not be empty")
        command = command.transition(
            CommandState.DISPATCHING,
            approved_by=approver,
        )
        self._save_command(command)
        await self._emit(
            "domain.command.approved",
            command.intent.resource_id,
            {"command_id": command.command_id, "approved_by": approver},
            correlation_id=command.intent.correlation_id,
            causation_id=command.intent.intent_id,
        )
        return await self._dispatch(command)

    async def reject(
        self,
        command_id: str,
        *,
        rejector_id: str,
        reason: str,
    ) -> Command:
        command = self.command(command_id)
        if command.state is not CommandState.PENDING_APPROVAL:
            raise DomainRuntimeError(
                f"command is not pending approval: {command.command_id}"
            )
        rejector = str(rejector_id or "").strip()
        if not rejector:
            raise ValueError("rejector_id must not be empty")
        rejection_reason = str(reason or "").strip()
        if not rejection_reason:
            raise ValueError("rejection reason must not be empty")
        rejected = command.transition(
            CommandState.REJECTED,
            rejected_by=rejector,
            rejection_reason=rejection_reason,
        )
        self._save_command(rejected)
        await self._emit(
            "domain.command.rejected",
            rejected.intent.resource_id,
            {
                "command_id": rejected.command_id,
                "intent_id": rejected.intent.intent_id,
                "rejected_by": rejector,
                "reason": rejection_reason,
            },
            correlation_id=rejected.intent.correlation_id,
            causation_id=rejected.intent.intent_id,
        )
        return rejected

    async def _dispatch(self, command: Command) -> Command:
        driver = self._drivers[command.driver_id]
        command = command.transition(
            CommandState.DISPATCHING,
            dispatched_at=utc_now(),
        )
        self._save_command(command)
        await self._emit(
            "domain.command.dispatching",
            command.intent.resource_id,
            {"command_id": command.command_id, "driver_id": command.driver_id},
            correlation_id=command.intent.correlation_id,
            causation_id=command.command_id,
        )
        try:
            result = await driver.execute(command)
        except Exception as exc:
            failed = command.transition(CommandState.FAILED, error=str(exc))
            self._save_command(failed)
            await self._command_event(failed)
            return failed
        if not result.accepted:
            failed = command.transition(CommandState.FAILED, error=result.error)
            self._save_command(failed)
            await self._command_event(failed)
            return failed
        for product in result.products:
            await self.record_product(product)
        state = (
            CommandState.ACKNOWLEDGED
            if result.expected_state
            else CommandState.CONFIRMED
        )
        output = dict(result.output)
        if result.products:
            output["product_ids"] = [
                product.product_id for product in result.products
            ]
        updated = command.transition(
            state,
            external_id=result.external_id,
            expected_state=result.expected_state,
            output=output,
        )
        self._save_command(updated)
        await self._command_event(updated)
        if state is CommandState.ACKNOWLEDGED:
            await self._reconcile(command.intent.resource_id)
            return self._commands[command.command_id]
        return updated

    async def _reconcile(self, resource_id: str) -> None:
        projection = self._projections.get(resource_id, {})
        for command in tuple(self._commands.values()):
            if (
                command.intent.resource_id != resource_id
                or command.state is not CommandState.ACKNOWLEDGED
            ):
                continue
            if all(
                key in projection
                and projection[key].value == expected
                and command.dispatched_at is not None
                and projection[key].received_at >= command.dispatched_at
                for key, expected in command.expected_state.items()
            ):
                confirmed = command.transition(CommandState.CONFIRMED)
                self._save_command(confirmed)
                await self._command_event(confirmed)

    def _restore(self) -> tuple[Command, ...]:
        if self.store is None:
            return ()

        observations = self.store.load_observations(self.domain_id)
        commands = self.store.load_commands(self.domain_id)
        events = self.store.load_events(self.domain_id)
        products = self.store.load_products(self.domain_id)

        for observation in observations:
            self.resource(observation.resource_id)
            if observation.observation_id in self._observation_ids:
                raise DomainRuntimeError(
                    f"duplicate persisted observation: {observation.observation_id}"
                )
            self._observation_ids.add(observation.observation_id)
            self._observations.append(observation)
            self._apply_projection(observation)

        recovered: list[Command] = []
        for command in commands:
            resource = self.resource(command.intent.resource_id)
            if resource.driver_id != command.driver_id:
                raise DomainRuntimeError(
                    f"persisted command driver does not own resource: "
                    f"{command.command_id}"
                )
            if command.command_id in self._commands:
                raise DomainRuntimeError(
                    f"duplicate persisted command: {command.command_id}"
                )
            if command.state is CommandState.DISPATCHING:
                command = command.transition(
                    CommandState.OUTCOME_UNKNOWN,
                    error="runtime stopped while command dispatch was in progress",
                )
                self.store.save_command(self.domain_id, command)
                recovered.append(command)
            self._commands[command.command_id] = command

        for product in products:
            if product.product_id in self._products:
                raise DomainRuntimeError(
                    f"duplicate persisted product: {product.product_id}"
                )
            self._products[product.product_id] = product

        self._events.extend(events)
        return tuple(recovered)

    def _apply_projection(self, observation: Observation) -> bool:
        epoch = str(
            observation.attributes.get("projection_epoch") or ""
        ).strip()
        current_epoch = self._projection_epochs.get(observation.resource_id)
        if epoch and epoch != current_epoch:
            if observation.quality is ObservationQuality.BAD:
                return False
            seen_epochs = self._projection_epoch_history.setdefault(
                observation.resource_id,
                set(),
            )
            if epoch in seen_epochs:
                return False
            seen_epochs.add(epoch)
            self._projection_epochs[observation.resource_id] = epoch
            self._projections[observation.resource_id] = {}
        current = self._projections.setdefault(observation.resource_id, {}).get(
            observation.metric
        )
        should_project = (
            observation.quality is not ObservationQuality.BAD
            and (
                current is None
                or observation.observed_at > current.observed_at
                or (
                    observation.observed_at == current.observed_at
                    and observation.received_at >= current.received_at
                )
            )
        )
        if not should_project:
            return False
        self._projections[observation.resource_id][observation.metric] = ProjectedValue(
            value=observation.value,
            unit=observation.unit,
            observed_at=observation.observed_at,
            received_at=observation.received_at,
            quality=observation.quality,
            observation_id=observation.observation_id,
        )
        return True

    def _save_command(self, command: Command) -> None:
        if self.store is not None:
            self.store.save_command(self.domain_id, command)
        self._commands[command.command_id] = command

    async def _command_event(self, command: Command) -> None:
        await self._emit(
            f"domain.command.{command.state.value}",
            command.intent.resource_id,
            {
                "command_id": command.command_id,
                "capability_id": command.intent.capability_id,
                "external_id": command.external_id,
                "error": command.error,
            },
            correlation_id=command.intent.correlation_id,
            causation_id=command.command_id,
        )

    async def _emit(
        self,
        event_type: str,
        subject_id: str,
        data: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            event_id=new_id("event"),
            event_type=event_type,
            subject_id=subject_id,
            occurred_at=utc_now(),
            data=data,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        if self.store is not None:
            self.store.append_event(self.domain_id, event)
        self._events.append(event)
        for selected_type, handler in tuple(self._subscribers):
            if selected_type is not None and selected_type != event_type:
                continue
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        return event
