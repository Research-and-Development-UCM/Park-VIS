import sys
import os
import logging
import asyncio
from loguru import logger
from .config import config

class LogStreamer:
    def __init__(self):
        self.queues = set()

    def add_queue(self, q):
        self.queues.add(q)

    def remove_queue(self, q):
        self.queues.discard(q)

    def broadcast(self, message):
        for q in list(self.queues):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

log_streamer = LogStreamer()
_logging_configured_level = None

def setup_logging(level=None):
    """Configures Loguru for console and file output, and intercepts standard logging."""
    global _logging_configured_level
    
    if level is None:
        level = config.LOG_LEVEL

    # If already configured at this exact level, avoid redundant reconfiguration
    if _logging_configured_level == level:
        return
    _logging_configured_level = level
    
    # Remove default handler
    logger.remove()

    # 1. Console Handler (stderr is None in Windows GUI-subsystem apps)
    if sys.stderr is not None:
        logger.add(
            sys.stderr, 
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level=level,
            colorize=True
        )

    # 2. File Handler
    log_dir = config.LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    logger.add(
        os.path.join(log_dir, "vulture.log"),
        rotation="10 MB",
        retention=f"{config.LOG_RETENTION_DAYS} days",
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        compression="zip"
    )

    # 3. Log Streamer Sink (for WebSockets)
    def streamer_sink(message):
        # We send the formatted text but could also send JSON
        log_streamer.broadcast(message.strip())

    logger.add(streamer_sink, level="DEBUG")

    # 4. Intercept standard logging
    class InterceptHandler(logging.Handler):
        def emit(self, record):
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            frame, depth = logging.currentframe(), 2
            while frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1

            logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    
    # Silence some very noisy third party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("websockets.protocol").setLevel(logging.INFO)

    logger.debug("Park VIS Logging Engine Initialized")

def setup_otel(app=None):
    """Configures OpenTelemetry for distributed tracing if enabled."""
    from .config import config
    if not config.OTEL_ENABLED:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        vulture_logger.info("Setting up OpenTelemetry for service: {name}", name=config.OTEL_SERVICE_NAME)

        resource = Resource.create({"service.name": config.OTEL_SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        
        # Jaeger OTLP Exporter (via gRPC)
        try:
            otlp_exporter = OTLPSpanExporter(endpoint=config.OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            vulture_logger.info("OTLP Exporter configured for {end}", end=config.OTEL_EXPORTER_OTLP_ENDPOINT)
        except Exception as e:
            vulture_logger.warning("Failed to setup OTLP Exporter, falling back to Console: {e}", e=e)
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        
        trace.set_tracer_provider(provider)

        if app:
            FastAPIInstrumentor.instrument_app(app)
        
        # Globally instrument SQLAlchemy engines
        SQLAlchemyInstrumentor().instrument()
        
        vulture_logger.info("OpenTelemetry Instrumentation enabled (Service: {name}, Endpoint: {end})", 
                           name=config.OTEL_SERVICE_NAME, end=config.OTEL_EXPORTER_OTLP_ENDPOINT)
    except Exception as e:
        vulture_logger.error("Failed to initialize OpenTelemetry: {e}", e=e)

# Export a default instance
vulture_logger = logger
