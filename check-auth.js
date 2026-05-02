// check-auth.js - Auto-login and session persistence
const API_URL = '/api';

async function checkAndRestoreSession() {
    try {
        // First try to get current user from server
        const response = await fetch(`${API_URL}/auth/me`, {
            credentials: 'include'
        });
        
        if (response.ok) {
            const user = await response.json();
            localStorage.setItem('chylnx_user', JSON.stringify(user));
            return user;
        }
        
        // If server says not logged in, check localStorage for remember me
        const rememberMe = localStorage.getItem('remember_me');
        const savedUser = localStorage.getItem('chylnx_user');
        
        if (rememberMe === 'true' && savedUser) {
            const user = JSON.parse(savedUser);
            // Try to restore session
            const restoreRes = await fetch(`${API_URL}/auth/restore-session`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ email: user.email })
            });
            
            if (restoreRes.ok) {
                const restoredUser = await restoreRes.json();
                localStorage.setItem('chylnx_user', JSON.stringify(restoredUser.user));
                return restoredUser.user;
            }
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
    localStorage.removeItem('chylnx_user');
    localStorage.removeItem('remember_me');
    window.location.href = '/login.html';
}

// Export for use in other files (if using modules)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { checkAndRestoreSession, ensureAuthenticated, logout };
}