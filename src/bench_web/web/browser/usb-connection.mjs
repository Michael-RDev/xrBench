// Kept independent of imports so the hosted bundle can use this exact implementation.
export async function connectUsb(device, signal, message = () => {}, onTrace = () => {}, dependencies) {
  const {Adb, AdbDaemonTransport, ADB_DEFAULT_AUTHENTICATORS, AdbAuthType,
    AdbWebCredentialStore, ConnectionError, connectionError} = dependencies;
  signal?.throwIfAborted();
  if (this._connectionAttempt || this._usbCleanup) throw new ConnectionError('usb_busy',
    'The previous USB connection is still closing. Reconnect the USB cable and reload this website before retrying. Saved results are preserved.');
  if (this.adb && this.device?.serial === device.serial) return;

  const previous = closeUsb.call(this), previousCleanup = this._usbCleanup;
  const attempt = {abandoned: false, transport: null};
  this._connectionAttempt = attempt;
  const started = performance.now(), sequence = this._connectionSequence = (this._connectionSequence || 0) + 1;
  let stage = 'opening', timer, operation, rejectStopped, receivedConnect = false, tracing = true;
  const emit = (event, packet) => {
    if (!tracing) return;
    const entry = {attempt: sequence, event, stage, elapsed_ms: Math.round(performance.now() - started)};
    if (packet) {
      entry.command = packet.command === 0x48545541 ? 'AUTH' : packet.command === 0x4e584e43 ? 'CNXN' : 'OTHER';
      if (entry.command === 'AUTH' && [1, 2, 3].includes(packet.arg0)) entry.auth_type = packet.arg0;
    }
    // Diagnostics must never change transport behavior or include arbitrary error text.
    try {onTrace(entry);} catch {}
  };
  const progress = (next, text) => {
    if (attempt.abandoned) return;
    stage = next; emit('stage'); message(text);
  };
  const stopped = new Promise((resolve, reject) => {rejectStopped = reject;});
  const closeRaw = () => Promise.resolve().then(() => device.raw.close());
  attempt.cancel = reason => {
    if (attempt.abandoned) return;
    attempt.abandoned = true; attempt.reason = reason;
    emit(reason?.name === 'AbortError' ? 'canceled' : 'failed');
    rejectStopped(reason);
    if (this.device === device) this.device = null;
    // Reject the caller immediately. Keep ownership until both the abandoned
    // operation and USB cleanup settle, including an open/authentication that
    // completes late. Nothing from that attempt can close a newer connection.
    const closing = closeRaw();
    const cleanup = (async () => {
      await Promise.allSettled([closing, operation]);
      await previousCleanup;
      try {await attempt.transport?.close();} finally {
        if (device.raw.opened) await device.raw.close();
      }
      if (device.raw.opened) throw Error('USB cleanup did not release the device');
    })();
    this._usbCleanup = cleanup;
    cleanup.then(() => {
      emit('cleanup_complete'); tracing = false;
      if (this._usbCleanup === cleanup) this._usbCleanup = null;
      if (this._connectionAttempt === attempt) this._connectionAttempt = null;
    }, () => {emit('cleanup_blocked'); tracing = false;});
  };
  const abort = () => attempt.cancel(signal.reason || new DOMException('Connection canceled', 'AbortError'));
  const expire = () => {
    const [code, text] = stage === 'approval'
      ? ['approval_timeout', 'No connection response arrived after the browser key was sent. If you already approved USB debugging, reconnect USB and retry; the connection trace records the last completed step.']
      : stage === 'credentials'
        ? ['credential_timeout', 'The browser could not read or prepare its USB debugging key. Reload this website, then retry.']
        : ['usb_timeout', stage === 'opening'
          ? 'Opening the headset USB interface timed out. Close other headset tools, reconnect the USB cable, then retry.'
          : stage === 'authenticating'
            ? 'The headset sent CNXN, but the browser did not finish connecting. Reconnect USB and retry; your saved results are preserved.'
            : stage === 'sending_key'
              ? 'Sending the browser USB debugging key did not finish. Reconnect USB and retry.'
              : 'The headset did not finish the USB handshake. Reconnect USB and retry; your saved results are preserved.'];
    attempt.cancel(new ConnectionError(code, text));
  };
  const assertActive = () => {if (attempt.abandoned) throw attempt.reason;};
  // Observe the actual packet streams, forwarding the original Consumable.
  // Completion includes its consumed promise (the USB writes), not just the
  // enqueue. A zero high-water mark avoids adding speculative packet reads.
  const observe = connection => {
    const reader = connection.readable.getReader(), writer = connection.writable.getWriter();
    return {
      readable: new ReadableStream({
        async pull(controller) {
          try {
            const result = await reader.read();
            if (result.done) {reader.releaseLock(); controller.close(); return;}
            if (!attempt.abandoned) {
              emit('packet_received', result.value);
              if (result.value.command === 0x4e584e43) {
                receivedConnect = true;
                progress('authenticating', 'Headset connection response received · finishing browser authentication…');
              }
            }
            controller.enqueue(result.value);
          } catch (error) {reader.releaseLock(); controller.error(error);}
        },
        async cancel(reason) {try {await reader.cancel(reason);} finally {reader.releaseLock();}},
      }, {highWaterMark: 0}),
      writable: new WritableStream({
        async write(chunk) {
          const packet = chunk.value || chunk;
          // A sink can reject both write() and consumed. Observe both promises
          // immediately so the second rejection never becomes unhandled.
          chunk.consumed?.catch(() => {});
          if (!attempt.abandoned) emit('packet_write_started', packet);
          try {
            await writer.write(chunk);
            await chunk.consumed;
            if (!attempt.abandoned) {
              emit('packet_write_complete', packet);
              if (packet.command === 0x48545541 && packet.arg0 === AdbAuthType.PublicKey && !receivedConnect) {
                clearTimeout(timer); timer = setTimeout(expire, 180000);
                progress('approval', 'Browser key sent · allow USB debugging inside the headset if prompted · waiting up to 3 minutes…');
              }
            }
          } catch (error) {if (!attempt.abandoned) emit('packet_write_failed', packet); throw error;}
        },
        async close() {try {await writer.close();} finally {writer.releaseLock();}},
        async abort(reason) {try {await writer.abort(reason);} finally {writer.releaseLock();}},
      }),
    };
  };
  timer = setTimeout(expire, 60000);
  operation = Promise.resolve().then(async () => {
    await previous; assertActive();
    try {await previousCleanup;} catch {
      throw new ConnectionError('usb_busy', 'The previous USB connection could not release its interface. Reconnect USB and reload this website. Saved results are preserved.');
    }
    assertActive(); this.device = device;
    progress('opening', 'Opening the headset USB interface…');
    const connection = await device.connect(); assertActive();
    progress('handshake', 'USB opened · waiting for the headset to respond…');
    const credentials = this.credentials ||= new AdbWebCredentialStore('xrBench@' + location.host);
    const credentialStore = {
      async *iterateKeys() {
        progress('credentials', 'Reading this browser’s saved USB debugging key…');
        for await (const key of credentials.iterateKeys()) {
          assertActive(); progress('handshake', 'Checking this browser’s USB debugging permission…'); yield key;
        }
        assertActive(); progress('handshake', 'Checking this browser’s USB debugging permission…');
      },
      async generateKey() {
        progress('credentials', 'Preparing this browser’s USB debugging key…');
        const key = await credentials.generateKey(); assertActive();
        progress('handshake', 'Preparing the USB debugging request…'); return key;
      },
    };
    const authenticators = ADB_DEFAULT_AUTHENTICATORS.map(authenticate => async function* (store, request) {
      for await (const packet of authenticate(store, request)) {
        assertActive();
        if (packet.arg0 === AdbAuthType.PublicKey) progress('sending_key', 'Browser USB debugging key prepared · sending to the headset…');
        yield packet;
      }
    });
    attempt.transport = await AdbDaemonTransport.authenticate({serial: device.serial, connection: observe(connection), credentialStore, authenticators});
    // A late transport may disconnect before it can be adopted by Adb.
    attempt.transport.disconnected?.catch(() => {});
    assertActive(); emit('authenticate_complete'); return attempt.transport;
  });
  signal?.addEventListener('abort', abort, {once: true});
  try {
    if (signal?.aborted) abort();
    const transport = await Promise.race([operation, stopped]);
    assertActive(); signal?.throwIfAborted();
    const current = new Adb(transport);
    if (!current.subprocess.shellProtocol) throw Error('This headset does not support the required Android shell protocol.');
    this.adb = current;
    current.disconnected.then(() => {if (this.adb === current) this.adb = null;}, () => {if (this.adb === current) this.adb = null;});
    emit('connected'); tracing = false;
    if (this._connectionAttempt === attempt) this._connectionAttempt = null;
  } catch (error) {
    attempt.cancel(error);
    if (error?.name === 'AbortError') throw error;
    throw connectionError(error);
  } finally {clearTimeout(timer); signal?.removeEventListener('abort', abort);}
}

export async function closeUsb() {
  if (this._connectionAttempt) {
    this._connectionAttempt.cancel(new DOMException('USB connection closed', 'AbortError'));
    return;
  }
  if (this._usbCleanup) return;
  const adb = this.adb, device = this.device;
  this.adb = null; this.device = null;
  if (!adb && !device) return;
  // Closing raw USB concurrently also unblocks a dispatcher waiting on I/O.
  const cleanup = Promise.allSettled([
    Promise.resolve().then(() => adb?.close()),
    Promise.resolve().then(() => {if (device?.raw.opened) return device.raw.close();}),
  ]).then(() => {if (device?.raw.opened) throw Error('USB cleanup did not release the device');});
  this._usbCleanup = cleanup;
  cleanup.then(() => {if (this._usbCleanup === cleanup) this._usbCleanup = null;}, () => {});
  await cleanup.catch(() => {});
}
