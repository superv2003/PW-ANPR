import asyncio
import json
import argparse
from lpr_engine import pipeline

async def main():
    parser = argparse.ArgumentParser(description="Test Parkwiz ANPR Pipeline on live RTSP")
    parser.add_argument("--ip", default="192.168.1.152", help="Camera IP Address")
    parser.add_argument("--user", default="admin", help="RTSP Username")
    parser.add_argument("--password", default="Parkwiz@2022", help="RTSP Password")
    args = parser.parse_args()
    
    print(f"Testing RTSP connection for camera IP: {args.ip}")
    print("Initiating ANPR processing pipeline...")
    
    try:
        result = await pipeline.process(
            camera_ip=args.ip,
            rtsp_user=args.user,
            rtsp_pass=args.password,
            lane_number="01"
        )
        
        print("\n" + "="*40)
        print("FINAL RESULT JSON:")
        print("="*40)
        print(json.dumps(result, indent=2))
        
    except Exception as e:
        print(f"Error executing pipeline: {e}")

if __name__ == "__main__":
    asyncio.run(main())
