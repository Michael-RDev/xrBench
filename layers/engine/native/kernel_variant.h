#pragma once
namespace xrbench {
// Selected per model on the inference worker, never by device registration.
inline thread_local bool candidate_kernel = false;
inline thread_local unsigned candidate_selections = 0;
inline constexpr unsigned candidate_tile_m = XRGPU_CANDIDATE_TILE_M;
}
