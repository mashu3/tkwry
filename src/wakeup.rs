//! Tk main-loop wakeup pipe (one byte per drain; coalesce when full).

use std::sync::atomic::{AtomicI32, Ordering};

#[cfg(unix)]
fn set_write_fd_nonblocking(fd: i32) {
    if fd < 0 {
        return;
    }
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 {
        return;
    }
    let _ = unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) };
}

#[cfg(windows)]
fn set_write_fd_nonblocking(fd: i32) {
    if fd < 0 {
        return;
    }
    const PIPE_NOWAIT: u32 = 0x0000_0001;
    unsafe {
        let handle = libc::_get_osfhandle(fd);
        if handle == -1 {
            return;
        }
        let mut mode = PIPE_NOWAIT;
        let _ = SetNamedPipeHandleState(
            handle as *mut libc::c_void,
            &mut mode,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        );
    }
}

#[cfg(not(any(unix, windows)))]
fn set_write_fd_nonblocking(_fd: i32) {}

/// Call when Python assigns the wakeup write end (``set_mac_wakeup_write_fd``).
pub fn configure_wakeup_write_fd(fd: i32) {
    set_write_fd_nonblocking(fd);
}

fn is_would_block(err: &std::io::Error) -> bool {
    #[cfg(unix)]
    {
        matches!(err.raw_os_error(), Some(libc::EAGAIN))
    }
    #[cfg(windows)]
    {
        matches!(
            err.raw_os_error(),
            Some(libc::EAGAIN) | Some(libc::EWOULDBLOCK)
        )
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = err;
        false
    }
}

/// Wake the Tk main loop (pipe byte; drained by Python ``after`` pump).
pub fn notify_wakeup(fd: &AtomicI32) {
    let fd = fd.load(Ordering::SeqCst);
    if fd < 0 {
        return;
    }
    set_write_fd_nonblocking(fd);
    let byte = 1u8;
    let wrote = unsafe { libc::write(fd, &byte as *const u8 as *const libc::c_void, 1) };
    if wrote == 1 {
        return;
    }
    // Windows ``PIPE_NOWAIT``: full buffer returns 0 bytes without an error.
    if wrote == 0 {
        return;
    }
    if wrote < 0 {
        let err = std::io::Error::last_os_error();
        if is_would_block(&err) {
            return;
        }
        eprintln!("tkwry: wakeup pipe write failed: {err}");
    }
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn SetNamedPipeHandleState(
        hNamedPipe: *mut libc::c_void,
        lpMode: *mut u32,
        lpMaxCollectionCount: *mut u32,
        lpCollectDataTimeout: *mut u32,
    ) -> i32;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    fn fill_pipe(write_fd: i32) -> u32 {
        let mut fills = 0_u32;
        loop {
            let wrote =
                unsafe { libc::write(write_fd, &[1u8] as *const u8 as *const libc::c_void, 1) };
            if wrote != 1 {
                break;
            }
            fills += 1;
            assert!(fills < 200_000, "pipe did not fill");
        }
        fills
    }

    #[test]
    fn notify_wakeup_does_not_block_when_pipe_full() {
        let mut fds = [0_i32; 2];
        let rc = unsafe { libc::pipe(fds.as_mut_ptr()) };
        assert_eq!(rc, 0, "pipe: {}", std::io::Error::last_os_error());
        let read_fd = fds[0];
        let write_fd = fds[1];
        configure_wakeup_write_fd(write_fd);

        let fills = fill_pipe(write_fd);

        let atomic_fd = AtomicI32::new(write_fd);
        let start = Instant::now();
        for _ in 0..2_000 {
            notify_wakeup(&atomic_fd);
        }
        assert!(
            start.elapsed() < Duration::from_millis(250),
            "notify_wakeup blocked for {:?} after {fills} fills",
            start.elapsed()
        );

        unsafe {
            libc::close(write_fd);
        }
        let mut drained = 0_u32;
        let mut buf = [0_u8; 64];
        loop {
            let n =
                unsafe { libc::read(read_fd, buf.as_mut_ptr() as *mut libc::c_void, buf.len()) };
            if n <= 0 {
                break;
            }
            drained += n as u32;
        }
        assert!(drained >= fills);

        unsafe {
            libc::close(read_fd);
        }
    }
}
