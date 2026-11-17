-- duo2-os: hardware decoding only for what the video core decodes.
--
-- mpv picks the hardware decoder by codec alone (hwdec-codecs in mpv.conf).
-- The iris driver (Gen1 firmware on the SM8350) refuses 10-bit and
-- interlaced streams only after it has seen the first headers: the file
-- then plays nothing ("Errors when loading file", mpv does not fall back)
-- and the driver's error path leaves a kernel warning behind. What tells
-- (pixel format, field order) is known to mpv only once a decoder is open,
-- and switching decoders mid-stream is not safe either, so: once the file
-- is open and before its decoders are created, ask ffprobe (~0.15 s; see PROBES) and
-- keep the hardware decoder only for 8-bit 4:2:0 progressive H.264 / HEVC
-- within the decoder's size range. Anything else, and anything ffprobe
-- cannot answer (a network stream, no ffprobe), is decoded in software, as
-- before. docs/hardware/video.md.
local codecs = { h264 = true, hevc = true }
local pixfmts = { yuv420p = true, yuvj420p = true }

-- A tiny probe (-probesize 32 -analyzeduration 0) is fast but too small for
-- some ordinary files (Blender's 2008 Big Buck Bunny H.264 answered
-- pix_fmt=unknown / field_order=unknown, so it was decoded in software at
-- 110 % CPU). ffprobe stops as soon as it has the answer, so a generous limit
-- costs nothing on a normal file; if it still says unknown, one more try
-- with a much larger one. Unknown after that stays software: it may be 10-bit
-- or interlaced, which the hardware decoder would turn into a dead file.
local PROBES = {
    { "5000000", "2000000" },
    { "50000000", "10000000" },
}

local function probe_once(path, size, duration)
    local r = mp.command_native({
        name = "subprocess", playback_only = false, capture_stdout = true, capture_stderr = true,
        args = { "ffprobe", "-v", "error", "-threads", "1", "-probesize", size, "-analyzeduration", duration,
                 "-select_streams", "V:0", "-show_entries", "stream=codec_name,pix_fmt,field_order,width,height",
                 "-of", "default=noprint_wrappers=1", "--", path },
    })
    if not r or r.status ~= 0 then
        return nil
    end
    local s = {}
    for k, v in (r.stdout or ""):gmatch("([%w_]+)=([^\n]*)") do
        s[k] = v
    end
    return s
end

local function known(v)
    return v and v ~= "unknown" and v ~= "N/A" and v ~= ""
end

local function probe(path)
    local s
    for _, p in ipairs(PROBES) do
        s = probe_once(path, p[1], p[2])
        if s and known(s.pix_fmt) and known(s.field_order) then
            break
        end
    end
    return s
end

mp.add_hook("on_preloaded", 50, function()
    if mp.get_property("hwdec", "no") == "no" then
        return
    end
    local video
    for _, t in ipairs(mp.get_property_native("track-list", {})) do
        if t.type == "video" and not t.albumart then
            video = t
            break
        end
    end
    if not video or not codecs[video.codec] then
        return
    end
    local path = mp.get_property("stream-open-filename", "")
    local info = not path:find("^%a[%w+.-]*://") and probe(path) or nil
    local w, h = tonumber(info and info.width), tonumber(info and info.height)
    if info and codecs[info.codec_name] and pixfmts[info.pix_fmt] and info.field_order == "progressive"
        and w and h and w >= 128 and h >= 128 and w <= 8192 and h <= 8192 then
        return
    end
    mp.msg.info(("software decoding: %s %s %s %sx%s"):format(video.codec,
        info and info.pix_fmt or "?", info and info.field_order or "?", w or "?", h or "?"))
    mp.set_property("file-local-options/hwdec", "no")
end)
