// duo2-os: YouTube in Chromium on the Surface Duo 2's video core.
//
// The SM8350 video core decodes H.264, HEVC and VP9 (iris, /dev/video0), but
// not AV1. YouTube serves AV1 to any browser that says it can play it, and
// Chromium can, in software (dav1d), so the hardware would sit idle. This
// script runs in the page's own world before YouTube's player does and
// answers "no" for the codecs below, in every API the player asks through:
// MediaSource.isTypeSupported (and ManagedMediaSource), canPlayType,
// navigator.mediaCapabilities.decodingInfo and WebCodecs'
// VideoDecoder.isConfigSupported. YouTube then picks VP9 (or H.264).
//
// HIDDEN is the list of codec strings to deny (a contentType or codec string
// containing one of them is refused). The -h264 variant (video/install-files.sh
// makes it) adds VP9 and VP8, so that only H.264 is left.
(() => {
    'use strict';
    const HIDDEN = ['av01'];

    const hidden = (type) => typeof type === 'string' &&
        HIDDEN.some((codec) => type.toLowerCase().includes(codec));

    for (const name of ['MediaSource', 'ManagedMediaSource', 'WebKitMediaSource']) {
        const ms = window[name];
        if (!ms || typeof ms.isTypeSupported !== 'function') {
            continue;
        }
        const isTypeSupported = ms.isTypeSupported.bind(ms);
        Object.defineProperty(ms, 'isTypeSupported', {
            configurable: true,
            writable: true,
            value: (type) => !hidden(type) && isTypeSupported(type),
        });
    }

    const canPlayType = HTMLMediaElement.prototype.canPlayType;
    Object.defineProperty(HTMLMediaElement.prototype, 'canPlayType', {
        configurable: true,
        writable: true,
        value: function (type) {
            return hidden(type) ? '' : canPlayType.call(this, type);
        },
    });

    const mc = navigator.mediaCapabilities;
    if (mc && typeof mc.decodingInfo === 'function') {
        const decodingInfo = mc.decodingInfo.bind(mc);
        Object.defineProperty(mc, 'decodingInfo', {
            configurable: true,
            writable: true,
            value: (config) => hidden(config && config.video && config.video.contentType)
                ? Promise.resolve({
                    supported: false, smooth: false, powerEfficient: false, configuration: config,
                })
                : decodingInfo(config),
        });
    }

    if (window.VideoDecoder && typeof VideoDecoder.isConfigSupported === 'function') {
        const isConfigSupported = VideoDecoder.isConfigSupported.bind(VideoDecoder);
        Object.defineProperty(VideoDecoder, 'isConfigSupported', {
            configurable: true,
            writable: true,
            value: (config) => hidden(config && config.codec)
                ? Promise.resolve({ supported: false, config })
                : isConfigSupported(config),
        });
    }

    if (window === window.top) {
        console.info(`duo2-os video codecs: not offered to this page: ${HIDDEN.join(', ')}`);
    }
})();
