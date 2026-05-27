# run_local.py

from scheduler.runner_local import run_local_pipeline
from utils.logger import setup_logger

if __name__ == "__main__":
    log = setup_logger()
    try:
        run_local_pipeline()
    except KeyboardInterrupt:
        log.info("Local pipeline interrupted by user.")
    except Exception as e:
        log.exception(f"Local pipeline failed: {e}")
        raise
