(() => {
    const form = document.getElementById('settingsForm');
    if (!form) return;

    const fields = {
        pattern: document.getElementById('filename_pattern'),
        separator: document.getElementById('filename_separator'),
        token: document.getElementById('auth_token'),
        threads: document.getElementById('thread_count'),
        metadata: document.getElementById('write_metadata'),
        poster: document.getElementById('download_poster'),
        seasonEnabled: document.getElementById('season_enabled'),
        seasonPattern: document.getElementById('season_pattern'),
        seasonSize: document.getElementById('season_size'),
        seasonOrder: document.getElementById('season_order')
    };

    function setSaving(saving) {
        document.getElementById('saveBtn').disabled = saving;
        document.getElementById('saveSpinner').classList.toggle('d-none', !saving);
    }

    function showMessage(message, success = true) {
        const element = document.getElementById('saveMessage');
        element.textContent = message;
        element.className = `save-message ${success ? 'text-success' : 'text-danger'}`;
    }

    async function loadSettings() {
        try {
            const response = await fetch('/api/settings');
            if (!response.ok) throw new Error('读取设置失败');
            const data = await response.json();
            fields.pattern.value = data.filename_pattern || '';
            fields.separator.value = data.filename_separator || '_';
            fields.token.value = '';
            fields.threads.value = data.thread_count || 3;
            fields.metadata.checked = ['1', 'true', 'yes', 'on'].includes(String(data.write_metadata || '0').toLowerCase());
            fields.poster.checked = Boolean(data.download_poster);
            fields.seasonEnabled.checked = Boolean(data.season_enabled);
            fields.seasonPattern.value = data.season_pattern || 'S{number:02d}';
            fields.seasonSize.value = data.season_size || 20;
            fields.seasonOrder.value = data.season_order || 'oldest';
            const tokenState = document.getElementById('tokenState');
            tokenState.textContent = data.auth_token_set ? '令牌已设置' : '尚未设置令牌';
            tokenState.classList.toggle('ready', Boolean(data.auth_token_set));
            fields.token.placeholder = data.auth_token_set ? '留空表示保留当前令牌' : '输入 _mfans_token';
            showMessage('');
        } catch (error) {
            showMessage('无法读取当前设置，请确认服务仍在运行。', false);
        }
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        setSaving(true);
        showMessage('正在保存……');
        const settings = {
            filename_pattern: fields.pattern.value.trim(),
            filename_separator: fields.separator.value || '_',
            auth_token: fields.token.value.trim(),
            thread_count: Number.parseInt(fields.threads.value, 10) || 3,
            write_metadata: fields.metadata.checked,
            download_poster: fields.poster.checked,
            season_enabled: fields.seasonEnabled.checked,
            season_pattern: fields.seasonPattern.value.trim() || 'S{number:02d}',
            season_size: Number.parseInt(fields.seasonSize.value, 10) || 20,
            season_order: fields.seasonOrder.value
        };
        try {
            const response = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.status !== 'success') throw new Error(data.error || '保存失败');
            fields.token.value = '';
            showMessage('设置已保存，下一次下载会自动生效。');
            await loadSettings();
            showMessage('设置已保存，下一次下载会自动生效。');
        } catch (error) {
            showMessage(`保存失败：${error.message}`, false);
        } finally {
            setSaving(false);
        }
    });

    const modal = document.getElementById('settingsModal');
    if (modal) {
        modal.addEventListener('show.bs.modal', loadSettings);
    } else {
        loadSettings();
    }
})();
