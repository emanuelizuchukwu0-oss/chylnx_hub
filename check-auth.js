// check-auth.js
const API_URL = '/api';

async function checkAndRestoreSession() {
    try {
        const response = await fetch(`${API_URL}/auth/me`, {
            credentials: 'include',
            headers: { 'Cache-Control': 'no-cache' }
        });
        
        if (response.ok) {
            const user = await response.json();
            localStorage.setItem('chylnx_user', JSON.stringify(user));
            return user;
        }
        return null;
    } catch (error) {
        console.error('Session check error:', error);
        return null;
    }
}

async function ensureAuthenticated(redirectTo = '/login.html') {
    const user = await checkAndRestoreSession();
    if (!user) {
        window.location.href = redirectTo;
        return null;
    }
    return user;
}

async function logout() {
    await fetch(`${API_URL}/auth/logout`, { method: 'POST', credentials: 'include' });
    localStorage.clear();
    window.location.href = '/login.html';
}