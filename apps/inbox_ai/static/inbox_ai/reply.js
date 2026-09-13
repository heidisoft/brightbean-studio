/* Loaded once by the optional inbox page overrides, before Alpine starts. Alpine also
   initializes this component when HTMX replaces the message detail panel. */
document.addEventListener('alpine:init', () => {
    Alpine.data('inboxReplyAI', () => ({
        open: false,
        busy: false,
        article: '',
        suggestion: '',
        error: '',
        controller: null,
        destroy() {
            this.controller?.abort();
        },
        async generate(style) {
            if (this.busy) return;
            this.busy = true;
            this.error = '';
            this.controller = new AbortController();
            const timer = setTimeout(() => this.controller?.abort(), 25000);
            try {
                const token = this.$el.closest('form').querySelector('[name=csrfmiddlewaretoken]').value;
                const response = await fetch(this.$root.dataset.generateUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {'X-CSRFToken': token, 'Accept': 'application/json'},
                    body: new URLSearchParams({style, article_text: this.article}),
                    signal: this.controller.signal,
                });
                if (response.redirected || response.status === 401 || response.status === 403) {
                    throw new Error('Your session expired or you do not have permission. Refresh the inbox and try again.');
                }
                if (!response.headers.get('content-type')?.includes('application/json')) {
                    throw new Error('Could not generate a reply. Refresh the inbox and try again.');
                }
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Generation failed. Please try again.');
                if (typeof data.reply !== 'string' || !data.reply.trim()) {
                    throw new Error('No reply was returned. Please try again.');
                }
                this.suggestion = data.reply;
            } catch (error) {
                this.error = error.name === 'AbortError'
                    ? 'Generation timed out. Please try again.'
                    : error instanceof TypeError
                        ? 'Could not reach the server. Please try again.'
                        : error.message;
            } finally {
                clearTimeout(timer);
                this.busy = false;
                this.controller = null;
            }
        },
    }));
});
