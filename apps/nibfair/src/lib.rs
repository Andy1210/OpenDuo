//! Nibfair's rules: everything that can be tested without a window.
//!
//! The games' logic lives here, free of SDL: the pond (fishing: the bite and
//! the line's tension), the slicing range (strokes, force, the eraser's
//! shield), the potter's wheel (the profile, the tool, the match with an
//! order), the haptic pulse requests, the strings and the save file. The
//! binary (main.rs and its modules) draws them and feeds them the pen.

pub mod fish;
pub mod frames;
pub mod geom;
pub mod haptic;
pub mod pen;
pub mod pottery;
pub mod rng;
pub mod save;
pub mod slice;
pub mod strings;
pub mod tween;
