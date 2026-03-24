# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A single-file static website (`index.html`) that displays "hello world" with each letter in a distinct color and a CSS dancing animation.

## Development

No build tools, dependencies, or package manager. Open `index.html` directly in a browser to preview changes.

## Architecture

Everything lives in `index.html`:
- Each letter is a `<span>` with an inline color style and staggered `animation-delay`
- A single `@keyframes dance` drives the bounce/rotate motion for all letters
- Layout is flexbox-centered on a light blue (`#add8e6`) background
