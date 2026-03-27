import sys
import logging
from blaxel.core import SyncSandboxInstance
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_setup")

def verify():
    logger.info("Checking Blaxel SDK installation...")
    try:
        import blaxel
        logger.info(f"Blaxel SDK installed: {blaxel.__version__}")
    except ImportError:
        logger.error("Blaxel SDK not found. Please install requirements.")
        return False

    logger.info("Testing Blaxel connection and Sandbox creation...")
    try:
        # Create a small sandbox
        sandbox_name = f"verify-{int(time.time())}"
        sandbox = SyncSandboxInstance.create(
            {"name": sandbox_name, "image": "blaxel/base-image:latest", "region": "us-pdx-1"}
        )
        logger.info(f"Sandbox {sandbox_name} created successfully.")
        
        # Run a simple command
        logger.info("Running test command...")
        result = sandbox.process.exec({"command": "echo 'Hello Blaxel'"})
        wait_res = sandbox.process.wait(result.name)
        
        if wait_res.exit_code == 0 and "Hello Blaxel" in wait_res.stdout:
            logger.info("Command execution verified.")
        else:
            logger.error(f"Command execution failed. Code: {wait_res.exit_code}, Output: {wait_res.stdout}")
            sandbox.delete()
            return False
            
        logger.info("Cleaning up...")
        sandbox.delete()
        logger.info("Verification passed!")
        return True

    except Exception as e:
        logger.error(f"Verification failed: {e}")
        return False

if __name__ == "__main__":
    if verify():
        sys.exit(0)
    else:
        sys.exit(1)
