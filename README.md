# Multichannel_Transcription-AWS_transcribe

## Overview
This project implements a real-time, multi-user speech transcription system using:
AWS Transcribe Streaming API
FastAPI (WebSocket backend)
Browser-based microphone capture
ngrok (for external access during development)
Multiple participants can join a shared room and stream live audio from their browsers. Each participant’s audio is streamed to AWS Transcribe, and transcripts are broadcast in real time to everyone in the same room.
This is a lightweight backend-focused implementation — no frontend framework, no heavy dependencies — designed to demonstrate clean streaming architecture.

## Architecture
Browser (Mic)
    ↓
WebSocket (FastAPI)
    ↓
AWS Transcribe Streaming
    ↓
Transcription Events
    ↓
Room Broadcast (WebSocket)

Each WebSocket connection creates a streaming transcription session.
Transcripts are scoped by ?room=<name> query parameter.

## Tech Stack

Python 3.9+
FastAPI
Uvicorn (ASGI server)
amazon-transcribe (AWS SDK)
awscrt
python-dotenv
AWS Transcribe Streaming
ngrok (dev only)

## Features

Real-time streaming transcription
Multi-user room-based sessions
WebSocket audio streaming
AWS streaming session per participant
Transcript broadcast to all participants in a room
Minimal, clean backend architecture

## Prerequisites

Python 3.9+
AWS account
IAM permission: transcribe:StartStreamTranscription
AWS credentials configured (CLI or .env)

## Setup
1. Clone
git clone https://github.com/<your-username>/Multichannel_Transcription-AWS_transcribe.git
cd Multichannel_Transcription-AWS_transcribe
2. Create Virtual Environment
python -m venv venv

3. Activate:
venv\Scripts\activate, pip install -r requirements.txt, python server.py

4. Open:
http://localhost:8000

5. Expose local server:
ngrok http 8000
Use the HTTPS link generated: https://xxxxx.ngrok-free.app/?room=demo
All participants must use: The same ngrok URL & The same ?room= value
