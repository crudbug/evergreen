import sys

import eventlet
import greenlet

from eventlet import event

__all__ = ['getcurrent', 'sleep', 'suspend', 'spawn', 'spawn_after', 'GreenThread']

get_current = greenlet.getcurrent

def sleep(seconds=0):
    """Yield control to another eligible coroutine until at least *seconds* have
    elapsed.

    *seconds* may be specified as an integer, or a float if fractional seconds
    are desired. Calling :func:`~greenthread.sleep` with *seconds* of 0 is the
    canonical way of expressing a cooperative yield. For example, if one is
    looping over a large list performing an expensive calculation without
    calling any socket methods, it's a good idea to call ``sleep(0)``
    occasionally; otherwise nothing else will run.
    """
    hub = eventlet.core.hub
    current = eventlet.core.current_greenlet
    assert hub.greenlet is not current, 'do not call blocking functions from the mainloop'
    timer = hub.call_later(seconds, current.switch)
    try:
        hub.switch()
    finally:
        timer.cancel()


def suspend(switch_back=True):
    """Suspend the current coroutine and yield control to the Hub for at least one
    event loop iteration. If switch back is False, the current task will not be resumed
    afterwards.
    """
    hub = eventlet.core.hub
    current = eventlet.core.current_greenlet
    assert hub.greenlet is not current, 'do not call blocking functions from the mainloop'
    if switch_back:
        hub.next_tick(current.switch)
    return hub.switch()


def spawn(func, *args, **kwargs):
    """Create a greenthread to run ``func(*args, **kwargs)``.  Returns a
    :class:`GreenThread` object which you can use to get the results of the
    call.

    Execution control returns immediately to the caller; the created greenthread
    is merely scheduled to be run at the next available opportunity.
    Use :func:`spawn_after` to  arrange for greenthreads to be spawned
    after a finite delay.
    """
    hub = eventlet.core.hub
    g = GreenThread(hub.greenlet)
    hub.next_tick(g.switch, func, args, kwargs)
    return g


def spawn_after(seconds, func, *args, **kwargs):
    """Spawns *func* after *seconds* have elapsed.  It runs as scheduled even if
    the current greenthread has completed.

    *seconds* may be specified as an integer, or a float if fractional seconds
    are desired. The *func* will be called with the given *args* and
    keyword arguments *kwargs*, and will be executed within its own greenthread.

    The return value of :func:`spawn_after` is a :class:`GreenThread` object,
    which can be used to retrieve the results of the call.

    To cancel the spawn and prevent *func* from being called,
    call :meth:`GreenThread.cancel` on the return value of :func:`spawn_after`.
    This will not abort the function if it's already started running, which is
    generally the desired behavior.  If terminating *func* regardless of whether
    it's started or not is the desired behavior, call :meth:`GreenThread.kill`.
    """
    hub = eventlet.core.hub
    g = GreenThread(hub.greenlet)
    hub.call_later(seconds, g.switch, func, args, kwargs)
    return g


class GreenThread(greenlet.greenlet):
    """The GreenThread class is a type of Greenlet which has the additional
    property of being able to retrieve the return value of the main function.
    Do not construct GreenThread objects directly; call :func:`spawn` to get one.
    """
    def __init__(self, parent):
        greenlet.greenlet.__init__(self, self.main, parent)
        self._exit_event = event.Event()

    def wait(self):
        """ Returns the result of the main function of this GreenThread.  If the
        result is a normal return value, :meth:`wait` returns it.  If it raised
        an exception, :meth:`wait` will raise the same exception (though the
        stack trace will unavoidably contain some frames from within the
        greenthread module)."""
        return self._exit_event.wait()

    def link(self, func, *curried_args, **curried_kwargs):
        """ Set up a function to be called with the results of the GreenThread.

        The function must have the following signature::

            def func(gt, [curried args/kwargs]):

        When the GreenThread finishes its run, it calls *func* with itself
        and with the `curried arguments <http://en.wikipedia.org/wiki/Currying>`_ supplied at link-time.  If the function wants
        to retrieve the result of the GreenThread, it should call wait()
        on its first argument.

        Note that *func* is called within execution context of
        the GreenThread, so it is possible to interfere with other linked
        functions by doing things like switching explicitly to another
        greenthread.
        """
        self._exit_funcs = getattr(self, '_exit_funcs', [])
        self._exit_funcs.append((func, curried_args, curried_kwargs))
        # TODO: what to do if the event already fired?

    def main(self, function, args, kwargs):
        try:
            result = function(*args, **kwargs)
        finally:
            self._exit_event.set()
            self._resolve_links()

    def _resolve_links(self):
        # ca and ckw are the curried function arguments
        for f, ca, ckw in getattr(self, '_exit_funcs', []):
            f(self, *ca, **ckw)
        self._exit_funcs = [] # so they don't get called again

    def kill(self, *throw_args):
        """Kills the greenthread using :func:`kill`.  After being killed
        all calls to :meth:`wait` will raise *throw_args* (which default
        to :class:`greenlet.GreenletExit`)."""
        return kill(self, *throw_args)

    def cancel(self, *throw_args):
        """Kills the greenthread using :func:`kill`, but only if it hasn't
        already started running.  After being canceled,
        all calls to :meth:`wait` will raise *throw_args* (which default
        to :class:`greenlet.GreenletExit`)."""
        return cancel(self, *throw_args)

def cancel(g, *throw_args):
    """Like :func:`kill`, but only terminates the greenthread if it hasn't
    already started execution.  If the grenthread has already started
    execution, :func:`cancel` has no effect."""
    if not g:
        kill(g, *throw_args)

def kill(g, *throw_args):
    """Terminates the target greenthread by raising an exception into it.
    Whatever that greenthread might be doing; be it waiting for I/O or another
    primitive, it sees an exception right away.

    By default, this exception is GreenletExit, but a specific exception
    may be specified.  *throw_args* should be the same as the arguments to
    raise; either an exception instance or an exc_info tuple.

    Calling :func:`kill` causes the calling greenthread to cooperatively yield.
    """
    if g.dead:
        return
    hub = eventlet.core.hub
    if not g:
        # greenlet hasn't started yet and therefore throw won't work
        # on its own; semantically we want it to be as though the main
        # method never got called
        def just_raise(*a, **kw):
            if throw_args:
                raise throw_args[0], throw_args[1], throw_args[2]
            else:
                raise greenlet.GreenletExit()
        g.run = just_raise
        if isinstance(g, GreenThread):
            # it's a GreenThread object, so we want to call its main
            # method to take advantage of the notification
            try:
                g.main(just_raise, (), {})
            except:
                pass
    current = eventlet.core.current_greenlet
    if current is not hub.greenlet:
        # arrange to wake the caller back up immediately
        hub.call_later(0, current.switch)
    g.throw(*throw_args)

