// https://bugzilla.mozilla.org/show_bug.cgi?id=1129121
// firefox has issues with caching old media data
// add a varying parameter to ensure an up to date stream by reloading
// audioSource.attr('src', '/stream?cache-buster=' + Date.now());

import {state} from './update';
import {getState} from '../base';

/** Syncs the output stream with the newest state from the server. */
export function syncAudioStream() {
  if (!streamAvailable) {
    return;
  }
  const audio = <any>$('audio')[0];
  if (state.currentSong && !state.currentSong.streamUrl) {
    // a song is playing but it provided no stream url
    $('#stream-control').css('color', 'var(--red)');
  } else {
    $('#stream-control').css('color', 'var(--normal-text)');
  }
  if (!state.currentSong || !state.currentSong.streamUrl ||
      state.paused || !streamActive) {
    audio.pause();
    return;
  }
  const src = state.currentSong.streamUrl;
  if (audio.src != src) {
    audio.src = src;
    audio.load();
  }
  const targetTime = state.progress / 100 * state.currentSong.duration;
  const currentTime = audio.currentTime;
  if (Math.abs(targetTime - currentTime) > 1) {
    // only seek if the deviation is too big to avoid unnecessary cuts
    audio.currentTime = targetTime;
  }
  // loading pauses the audio element, play if streaming is active
  audio.play();
}

let streamAvailable = false;
let streamActive = false;

/** Adds handlers for the audio element. */
export function onReady() {
  const audio = $('audio');
  if (audio.length) {
    streamAvailable = true;
  }

  $('#stream-control').on('click tap', function(e) {
    if (streamActive) {
      streamActive = false;
      $('#stream-control').removeClass('fa-volume-up');
      $('#stream-control').addClass('fa-volume-mute');
    } else {
      streamActive = true;
      $('#stream-control').removeClass('fa-volume-mute');
      $('#stream-control').addClass('fa-volume-up');
      getState();
    }
    syncAudioStream();
  });
}

$(document).ready(() => {
  if (!window.location.pathname.endsWith('musiq/')) {
    return;
  }
  onReady();
});
