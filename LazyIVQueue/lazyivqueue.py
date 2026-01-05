import LazyIVQueue.config as AppConfig
from LazyIVQueue.utils.logger import logger, setup_logging

# Initialize logging
setup_logging(AppConfig.log_level, {"file": AppConfig.log_file, "function": True})
