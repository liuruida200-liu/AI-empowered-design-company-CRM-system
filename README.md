# AI-Empowered Design Company CRM System

An AI-assisted CRM and collaboration system for design-and-print companies that create banners, roll-up banners, posters, social media posts, and other custom marketing materials.

## Overview

Many design and production companies spend too much time going back and forth with customers before an order is ready for production. In many cases, customers do not begin with a complete or feasible plan. This creates long communication cycles between customers and sales staff just to finalize the draft, confirm design requirements, choose materials, and estimate cost and delivery time.

This project aims to solve that problem by embedding an LLM-powered AI assistant directly into the sales and customer group chat workflow.

## Problem

Design companies often face these challenges:

- Customers submit incomplete or unclear requirements
- Sales teams spend a large amount of time clarifying design details
- Quotation depends on past cases, materials, production effort, and partner capability
- Information is fragmented across sales chats, production chats, and historical orders
- Customers wait too long for simple answers such as pricing, feasibility, or progress updates

## Proposed Solution

We are building a CRM system with an embedded AI assistant that participates in both customer-facing and internal communication channels.

The AI assistant will help sales and operations teams by using company data and live production context to support faster, more accurate communication.

## Core Capabilities

### 1. Customer and Sales Chat Assistance
The AI assistant will be embedded in the shared group chat between customers and sales staff. It will be able to:

- Answer common questions instantly
- Help clarify incomplete customer requirements
- Suggest feasible design and material options
- Recommend examples from similar past orders
- Support sales staff in creating faster and more consistent replies

### 2. Historical Order Intelligence
The AI assistant will have access to past order records, including:

- Previous designs
- Material choices
- Price breakdowns
- Time spent on communication and production
- Outcomes from similar customer requests

This allows the assistant to provide reference cases and generate more realistic recommendations and quotations.

### 3. Quotation Support
Based on historical data and current requirements, the AI assistant can help with:

- Initial quotation estimates
- Material-based pricing suggestions
- Design and production cost references
- Feasibility checks before final confirmation

### 4. Manufacturing Partner Awareness
The assistant will also understand the capabilities of manufacturing partners, such as:

- Supported materials
- Production limits
- Available equipment or processes
- Turnaround constraints

This helps ensure that proposed solutions are not only attractive but also practical and manufacturable.

### 5. Production Progress Visibility
The AI assistant can also access internal production division chats and updates in order to:

- Track live production progress
- Answer customer questions about order status
- Reduce manual follow-up work from staff
- Provide faster and more transparent updates

## Vision

Our vision is to create a smart CRM system that does more than store customer data. It actively helps teams close orders faster, reduce communication overhead, improve customer experience, and connect sales with real production capability.

Instead of relying entirely on manual coordination, the AI assistant becomes a real-time support layer between:

- Customers
- Sales staff
- Designers
- Production teams
- Manufacturing partners

## Expected Benefits

- Faster requirement clarification
- Reduced workload for sales teams
- More accurate and consistent quotations
- Better use of past project knowledge
- Improved communication between customer-facing and production teams
- Faster response time for customer questions
- Higher operational efficiency overall

## Example Use Cases

- A customer asks for a roll-up banner but does not know the best material or size
- The AI suggests possible options based on past similar orders
- The AI provides a rough quote based on historical price breakdowns
- The AI checks whether manufacturing partners can produce the requested item
- The customer asks for progress, and the AI responds using live production updates

## Project Status

This project is currently in the planning and concept stage.

Initial focus areas include:

- CRM workflow design
- Chat-based AI assistant integration
- Historical order data retrieval
- Quote generation logic
- Production status synchronization
- Partner capability knowledge base

## Long-Term Goal

To build an AI-native CRM platform for custom design and print companies that combines sales support, production visibility, and company knowledge into one intelligent workflow.


Progress:
Database (db.py + schema.sql)

Added Room and RoomMember tables
Added room_id to Message so every message belongs to a specific room
Fixed the FK delete inconsistency between SQLAlchemy and SQL file


Backend (app.py)

Fixed the session bug in maybe_answer_with_llm — now opens its own fresh session
Added get_current_user shared dependency
Added 6 new room routes: create, list, list my rooms, join, leave, get messages, post message
All message routes are room-scoped and membership-gated
LLM bot replies into the correct room


WebSocket (websocket_manager.py)

Rebuilt from a flat list into a room-aware dictionary
Added switch_room() to move connections between rooms cleanly
Broadcasts only to connections inside the target room


Frontend (index.html + styles.css + app.js)

New two-column layout with sidebar and main chat area
Room list with join badges, active room highlight
Create room form, leave room button
Logged-in username shown in sidebar header
Last active room restored on refresh
Joined rooms persisted across refresh via GET /api/rooms/my
