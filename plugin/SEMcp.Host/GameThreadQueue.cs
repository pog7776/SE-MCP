using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Threading.Tasks;

namespace SEMcp.Host
{
    // Background threads enqueue work; the game thread drains it in Update() within a time budget.
    public sealed class GameThreadQueue
    {
        private readonly ConcurrentQueue<Action> queue = new ConcurrentQueue<Action>();
        private volatile bool closed;

        public Task<T> Run<T>(Func<T> work)
        {
            var completion = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
            if (closed)
            {
                completion.SetException(new ObjectDisposedException(nameof(GameThreadQueue)));
                return completion.Task;
            }

            queue.Enqueue(() =>
            {
                try
                {
                    completion.SetResult(work());
                }
                catch (Exception e)
                {
                    completion.SetException(e);
                }
            });
            return completion.Task;
        }

        // Game thread only. Always runs at least one item so a slow item can't starve the queue.
        public void Drain(double budgetMs)
        {
            var clock = Stopwatch.StartNew();
            while (queue.TryDequeue(out var action))
            {
                action();
                if (clock.Elapsed.TotalMilliseconds >= budgetMs)
                    break;
            }
        }

        // Game thread only (host Stop). Runs whatever is still queued so no caller is left waiting,
        // then rejects new work.
        public void Close()
        {
            closed = true;
            while (queue.TryDequeue(out var action))
                action();
        }
    }
}
