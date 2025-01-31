from typing import Dict, TYPE_CHECKING
import contextlib

import numpy as np

from catalyst.core.logger import ILogger
from catalyst.settings import SETTINGS

if SETTINGS.neptune_required:
    try:  # >1.0 package structure
        import neptune
        from neptune.utils import stringify_unsupported
        from neptune.handler import Handler
    except ImportError:  # <1.0 package structure
        import neptune.new as neptune
        from neptune.new.utils import stringify_unsupported
        from neptune.new.handler import Handler
if TYPE_CHECKING:
    from catalyst.core.runner import IRunner


def _prepare_metrics(metrics):
    conflict_keys = []
    processed_metrics = dict(metrics)
    for k in list(processed_metrics.keys()):
        if k.endswith("/std"):
            k_stripped = k[:-4]
            k_val = f"{k_stripped}/val"
            if k_val not in processed_metrics.keys():
                processed_metrics[k_val] = processed_metrics.pop(k_stripped)
    for k in processed_metrics:
        for j in processed_metrics:
            if j.startswith(k) and j != k and k not in conflict_keys:
                conflict_keys.append(k)
    for i in conflict_keys:
        processed_metrics[f"{i}_val"] = processed_metrics.pop(i)
    return processed_metrics


class NeptuneLogger(ILogger):
    """Neptune logger for parameters, metrics, images and other artifacts (videos, audio,
    model checkpoints, etc.).

    To get started with Neptune, see the
    `Neptune installation steps <https://docs.neptune.ai/setup/installation/>`_
    because you will need your API token and a project to log your Catalyst runs to.

    When the logger is created, a link to the Neptune run is printed to stdout.
    It looks like this:
    https://app.neptune.ai/common/catalyst-integration/e/CATALYST-1486

    For details, see the Catalyst integration guide in the
    `Neptune documentation <https://docs.neptune.ai/integrations/catalyst/>`_

    .. note::
        You can use the public api_token ``neptune.ANONYMOUS_API_TOKEN``
        (you will need to import `neptune` to use this) and set the project to
        ``common/catalyst-integration`` for testing without registration.

    Args:
        base_namespace: Optional, ``str``, root namespace where all the metadata tracked
            by the logger is stored. The default is "experiment".
        api_token: Optional, ``str``. Your Neptune API token. Read more about it in the
          `Neptune docs <https://docs.neptune.ai/setup/setting_api_token/>`_.
        project: Optional, ``str``. Name of the project to log runs to.
          It looks like this: "workspace-name/project-name".
        run: Optional, Neptune run object. Read more about resuming a run in the
          `Neptune docs <https://docs.neptune.ai/logging/to_existing_object/>`_.
          You can also pass a namespace handler object; for example, ``run["test"]``,
          in which case all metadata is logged under the "test" namespace inside the run.
        log_batch_metrics: boolean flag to log batch metrics
          (default: SETTINGS.log_batch_metrics or False).
        log_epoch_metrics: boolean flag to log epoch metrics
          (default: SETTINGS.log_epoch_metrics or True).
        neptune_run_kwargs: Optional, additional keyword arguments
          to be passed directly to the
          `neptune.init_run() <https://docs.neptune.ai/api/neptune/#init_run>`_
          function.

    Python API examples:

    .. code-block:: python

        from catalyst import dl

        runner = dl.SupervisedRunner()
        runner.train(
            ...
            loggers={
                "neptune": dl.NeptuneLogger(
                    project="my_workspace/my_project",
                    tags=["pretraining", "retina"],
                )
            }
        )

    .. code-block:: python

        from catalyst import dl

        class CustomRunner(dl.IRunner):
            # ...

            def get_loggers(self):
                return {
                    "console": dl.ConsoleLogger(),
                    "neptune": dl.NeptuneLogger(
                        project="my_workspace/my_project"
                    )
                }
            # ...

        runner = CustomRunner().run()
    """

    def __init__(
        self,
        base_namespace=None,
        api_token=None,
        project=None,
        run=None,
        log_batch_metrics: bool = SETTINGS.log_batch_metrics,
        log_epoch_metrics: bool = SETTINGS.log_epoch_metrics,
        **neptune_run_kwargs,
    ):
        super().__init__(
            log_batch_metrics=log_batch_metrics, log_epoch_metrics=log_epoch_metrics
        )
        if base_namespace is None:
            self.base_namespace = "experiment"
        else:
            self.base_namespace = base_namespace
        self._api_token = api_token
        self._project = project
        self._neptune_run_kwargs = neptune_run_kwargs
        if run is None:
            self.run = neptune.init_run(
                project=self._project,
                api_token=self._api_token,
                **self._neptune_run_kwargs,
            )
        else:
            self.run = run
        with contextlib.suppress(ImportError, NameError, AttributeError):
            import catalyst.__version__ as version
            root_obj = self.run
            if isinstance(self.run, neptune.handler.Handler):
                root_obj = self.run.get_root_object()
            root_obj["source_code/integrations/neptune-catalyst"] = version

    @property
    def logger(self):
        """Internal logger/experiment/etc. from the monitoring system."""
        return self.run

    def _log_metrics(self, metrics: Dict[str, float], neptune_path: str, step: int):
        for key, value in metrics.items():
            self.run[neptune_path][key].append(value=float(value), step=step)

    def _log_image(self, image: np.ndarray, neptune_path: str):
        self.run[neptune_path].append(neptune.types.File.as_image(image))

    def _log_artifact(self, artifact: object, path_to_artifact: str, neptune_path: str):
        if artifact is not None:
            self.run[neptune_path].upload(neptune.types.File.as_pickle(artifact))
        elif path_to_artifact is not None:
            self.run[neptune_path].upload(path_to_artifact)

    def log_artifact(
        self,
        tag: str,
        runner: "IRunner",
        artifact: object = None,
        path_to_artifact: str = None,
        scope: str = None,
    ) -> None:
        """Logs arbitrary file (audio, video, csv, etc.) to Neptune."""
        if artifact is not None and path_to_artifact is not None:
            ValueError("artifact and path_to_artifact are mutually exclusive")
        if scope == "batch":
            neptune_path = "/".join(
                [
                    self.base_namespace,
                    "_artifacts",
                    f"epoch-{runner.epoch_step:04d}",
                    f"loader-{runner.loader_key}",
                    f"batch-{runner.batch_step:04d}",
                    tag,
                ]
            )
        elif scope == "loader":
            neptune_path = "/".join(
                [
                    self.base_namespace,
                    "_artifacts",
                    f"epoch-{runner.epoch_step:04d}",
                    f"loader-{runner.loader_key}",
                    tag,
                ]
            )
        elif scope == "epoch":
            neptune_path = "/".join(
                [
                    self.base_namespace,
                    "_artifacts",
                    f"epoch-{runner.epoch_step:04d}",
                    tag,
                ]
            )
        elif scope == "experiment" or scope is None:
            neptune_path = "/".join([self.base_namespace, "_artifacts", tag])
        self._log_artifact(artifact, path_to_artifact, neptune_path)

    def log_image(
        self,
        tag: str,
        image: np.ndarray,
        runner: "IRunner",
        scope: str = None,
    ) -> None:
        """Logs image to Neptune for current scope on current step."""
        if scope in {"batch", "loader"}:
            log_path = "/".join(
                [
                    self.base_namespace,
                    "_images",
                    f"epoch-{runner.epoch_step:04d}",
                    f"loader-{runner.loader_key}",
                    tag,
                ]
            )
        elif scope == "epoch":
            log_path = "/".join(
                [self.base_namespace, "_images", f"epoch-{runner.epoch_step:04d}", tag]
            )
        elif scope == "experiment" or scope is None:
            log_path = "/".join([self.base_namespace, "_images", tag])
        self._log_image(image, log_path)

    def log_hparams(self, hparams: Dict, runner: "IRunner" = None) -> None:
        """Logs hyper-parameters to Neptune."""
        self.run[self.base_namespace]["hparams"] = stringify_unsupported(hparams)

    def log_metrics(
        self,
        metrics: Dict[str, float],
        scope: str,
        runner: "IRunner",
    ) -> None:
        """Logs batch, epoch and loader metrics to Neptune."""
        if scope == "batch" and self.log_batch_metrics:
            neptune_path = "/".join([self.base_namespace, runner.loader_key, scope])
            self._log_metrics(
                metrics=metrics, neptune_path=neptune_path, step=runner.sample_step
            )
        elif scope == "loader" and self.log_epoch_metrics:
            neptune_path = "/".join([self.base_namespace, runner.loader_key, scope])
            self._log_metrics(
                metrics=_prepare_metrics(metrics),
                neptune_path=neptune_path,
                step=runner.epoch_step,
            )
        elif scope == "epoch" and self.log_epoch_metrics:
            loader_key = "_epoch_"
            prepared_metrics = _prepare_metrics(metrics[loader_key])
            neptune_path = "/".join([self.base_namespace, scope])
            if prepared_metrics:
                self._log_metrics(
                    metrics=prepared_metrics,
                    neptune_path=neptune_path,
                    step=runner.epoch_step,
                )
        elif scope == "experiment" or scope is None:
            self._log_metrics(metrics=metrics, neptune_path=self.base_namespace, step=0)

    def flush_log(self) -> None:
        """Flushes the loggers."""
        pass

    def close_log(self, scope: str = None) -> None:
        """Closes the loggers."""
        root_obj = self.run
        if isinstance(root_obj, Handler):
            root_obj = root_obj.get_root_object()
        root_obj.wait()


__all__ = ["NeptuneLogger"]
