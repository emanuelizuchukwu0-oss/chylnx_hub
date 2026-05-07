// check-auth.js
// Authentication & Session Management Utility
// Include this script in all pages that require authentication

(function() {
    'use strict';

    const API_URL = '/api';
    
    // Configuration
    const CONFIG = {
        SESSION_CHECK_INTERVAL: 5 * 60 * 1000, // Check session every 5 minutes
        SESSION_STORAGE_KEY: 'chylnx_user',
        LAST_ACTIVITY_KEY: 'chylnx_last_activity',
        SESSION_TIMEOUT: 30 * 60 * 1000, // 30 minutes inactivity timeout
        RETRY_ATTEMPTS: 3,
        RETRY_DELAY: 1000
    };

    // State
    let currentUser = null;
    let sessionCheckTimer = null;
    let isRefreshing = false;
    let refreshPromise = null;

    // =====================
    // UTILITY FUNCTIONS
    // =====================

    /**
     * Make authenticated API request with retry logic
     */
    async function apiFetch(url, options = {}, retries = CONFIG.RETRY_ATTEMPTS) {
        const defaultOptions = {
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache'
            }
        };

        const mergedOptions = {
            ...defaultOptions,
            ...options,
            headers: { ...defaultOptions.headers, ...options.headers }
        };

        for (let attempt = 0; attempt <= retries; attempt++) {
            try {
                const response = await fetch(url, mergedOptions);

                // Handle 401 - session expired
                if (response.status === 401) {
                    clearSession();
                    if (window.location.pathname !== '/login.html') {
                        window.location.href = '/login.html?expired=true';
                    }
                    throw new Error('Session expired');
                }

                return response;
            } catch (error) {
                if (error.message === 'Session expired') throw error;
                
                // Only retry on network errors
                if (attempt === retries) {
                    throw error;
                }
                
                console.warn(`API request failed (attempt ${attempt + 1}/${retries + 1}):`, error);
                await new Promise(resolve => setTimeout(resolve, CONFIG.RETRY_DELAY * (attempt + 1)));
            }
        }
    }

    /**
     * Update last activity timestamp
     */
    function updateActivity() {
        try {
            localStorage.setItem(CONFIG.LAST_ACTIVITY_KEY, Date.now().toString());
        } catch (error) {
            console.error('Failed to update activity:', error);
        }
    }

    /**
     * Check if session has timed out due to inactivity
     */
    function isSessionTimedOut() {
        try {
            const lastActivity = localStorage.getItem(CONFIG.LAST_ACTIVITY_KEY);
            if (!lastActivity) return false;
            
            const elapsed = Date.now() - parseInt(lastActivity, 10);
            return elapsed > CONFIG.SESSION_TIMEOUT;
        } catch {
            return false;
        }
    }

    /**
     * Clear all session data
     */
    function clearSession() {
        currentUser = null;
        if (sessionCheckTimer) {
            clearInterval(sessionCheckTimer);
            sessionCheckTimer = null;
        }
        
        try {
            localStorage.removeItem(CONFIG.SESSION_STORAGE_KEY);
            localStorage.removeItem(CONFIG.LAST_ACTIVITY_KEY);
        } catch (error) {
            console.error('Failed to clear session data:', error);
        }
    }

    /**
     * Store user data safely
     */
    function storeUserData(user) {
        if (!user) return;
        
        try {
            // Only store non-sensitive data
            const safeUserData = {
                email: user.email,
                fullName: user.fullName,
                paymentVerified: user.paymentVerified,
                isAdmin: user.isAdmin,
                displayName: user.displayName,
                lastUpdated: Date.now()
            };
            
            localStorage.setItem(CONFIG.SESSION_STORAGE_KEY, JSON.stringify(safeUserData));
            updateActivity();
        } catch (error) {
            console.error('Failed to store user data:', error);
        }
    }

    /**
     * Get cached user data from localStorage
     */
    function getCachedUser() {
        try {
            const cached = localStorage.getItem(CONFIG.SESSION_STORAGE_KEY);
            if (!cached) return null;
            
            const user = JSON.parse(cached);
            
            // Validate cached data structure
            if (!user.email || !user.lastUpdated) return null;
            
            // Check if cache is too old (30 minutes)
            const cacheAge = Date.now() - user.lastUpdated;
            if (cacheAge > CONFIG.SESSION_TIMEOUT) return null;
            
            return user;
        } catch (error) {
            console.error('Failed to parse cached user:', error);
            return null;
        }
    }

    // =====================
    // MAIN FUNCTIONS
    // =====================

    /**
     * Check and restore user session
     * Returns user object if authenticated, null otherwise
     */
    async function checkAndRestoreSession() {
        // Return cached user if available and fresh
        if (currentUser) {
            updateActivity();
            return currentUser;
        }

        // Check for session timeout
        if (isSessionTimedOut()) {
            console.log('Session timed out due to inactivity');
            clearSession();
            return null;
        }

        // Prevent multiple simultaneous refresh calls
        if (isRefreshing && refreshPromise) {
            return refreshPromise;
        }

        isRefreshing = true;
        refreshPromise = (async () => {
            try {
                const response = await apiFetch(`${API_URL}/auth/me`);
                
                if (response.ok) {
                    const user = await response.json();
                    
                    // Validate user data
                    if (!user || !user.email) {
                        throw new Error('Invalid user data received');
                    }
                    
                    currentUser = user;
                    storeUserData(user);
                    startSessionCheck();
                    
                    console.log('Session restored for:', user.email);
                    return user;
                }
                
                // Session invalid
                clearSession();
                return null;
                
            } catch (error) {
                console.error('Session check error:', error);
                
                // Try to use cached data as fallback
                const cachedUser = getCachedUser();
                if (cachedUser) {
                    console.log('Using cached user data as fallback');
                    currentUser = cachedUser;
                    return cachedUser;
                }
                
                clearSession();
                return null;
            } finally {
                isRefreshing = false;
                refreshPromise = null;
            }
        })();

        return refreshPromise;
    }

    /**
     * Ensure user is authenticated, redirect if not
     * @param {string} redirectTo - URL to redirect unauthenticated users
     * @returns {Promise<Object|null>} User object or null
     */
    async function ensureAuthenticated(redirectTo = '/login.html') {
        try {
            const user = await checkAndRestoreSession();
            
            if (!user) {
                // Build redirect URL with return path
                const currentPath = window.location.pathname;
                const returnParam = currentPath !== '/login.html' ? `?return=${encodeURIComponent(currentPath)}` : '';
                
                console.log('User not authenticated, redirecting to:', redirectTo + returnParam);
                window.location.href = redirectTo + returnParam;
                return null;
            }
            
            return user;
        } catch (error) {
            console.error('Authentication check failed:', error);
            window.location.href = redirectTo;
            return null;
        }
    }

    /**
     * Check if user has specific role/permission
     * @param {string} permission - 'admin' | 'verified' | 'payment'
     */
    async function hasPermission(permission) {
        const user = await checkAndRestoreSession();
        if (!user) return false;
        
        switch (permission) {
            case 'admin':
                return user.isAdmin === true;
            case 'verified':
                return user.paymentVerified === true;
            case 'payment':
                return user.paymentVerified === true || user.isAdmin === true;
            default:
                return false;
        }
    }

    /**
     * Ensure user has specific permission
     * @param {string} permission - Required permission
     * @param {string} redirectTo - URL to redirect if permission denied
     */
    async function requirePermission(permission, redirectTo = '/index.html') {
        const user = await checkAndRestoreSession();
        
        if (!user) {
            window.location.href = '/login.html';
            return false;
        }
        
        const hasAccess = await hasPermission(permission);
        
        if (!hasAccess) {
            console.warn(`User ${user.email} lacks permission: ${permission}`);
            window.location.href = redirectTo;
            return false;
        }
        
        return true;
    }

    /**
     * Logout user - clear session and redirect
     * @param {string} redirectTo - URL to redirect after logout
     */
    async function logout(redirectTo = '/login.html') {
        try {
            // Attempt server logout (fire and forget)
            await apiFetch(`${API_URL}/auth/logout`, { method: 'POST' });
        } catch (error) {
            console.error('Logout API error:', error);
            // Continue with local logout even if server request fails
        } finally {
            // Always clear local state
            clearSession();
            
            try {
                localStorage.clear();
                sessionStorage.clear();
            } catch (error) {
                console.error('Failed to clear storage:', error);
            }
            
            // Clear cookies (belt and suspenders approach)
            document.cookie.split(';').forEach(cookie => {
                const name = cookie.split('=')[0].trim();
                document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/`;
            });
            
            console.log('User logged out, redirecting to:', redirectTo);
            window.location.href = redirectTo;
        }
    }

    /**
     * Start periodic session checking
     */
    function startSessionCheck() {
        if (sessionCheckTimer) {
            clearInterval(sessionCheckTimer);
        }
        
        sessionCheckTimer = setInterval(async () => {
            if (document.hidden) return; // Don't check if tab is hidden
            
            try {
                const user = await checkAndRestoreSession();
                if (!user && window.location.pathname !== '/login.html') {
                    console.log('Session expired during periodic check');
                    clearSession();
                    window.location.href = '/login.html?expired=true';
                }
            } catch (error) {
                console.error('Periodic session check failed:', error);
            }
        }, CONFIG.SESSION_CHECK_INTERVAL);
    }

    /**
     * Get current user synchronously (may return cached/null)
     */
    function getCurrentUserSync() {
        return currentUser || getCachedUser();
    }

    /**
     * Refresh user data from server
     */
    async function refreshUserData() {
        isRefreshing = false; // Force fresh fetch
        refreshPromise = null;
        currentUser = null;
        return await checkAndRestoreSession();
    }

    // =====================
    // EVENT LISTENERS
    // =====================

    // Track user activity
    ['click', 'keypress', 'scroll', 'touchstart'].forEach(eventType => {
        document.addEventListener(eventType, updateActivity, { passive: true });
    });

    // Stop session checking when tab is hidden
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            if (sessionCheckTimer) {
                clearInterval(sessionCheckTimer);
                sessionCheckTimer = null;
            }
        } else {
            // Resume checking when tab becomes visible
            startSessionCheck();
        }
    });

    // Handle page unload
    window.addEventListener('beforeunload', () => {
        if (sessionCheckTimer) {
            clearInterval(sessionCheckTimer);
        }
    });

    // =====================
    // INITIALIZATION
    // =====================

    // Auto-check session on script load if not on login page
    if (window.location.pathname !== '/login.html') {
        checkAndRestoreSession().then(user => {
            if (user) {
                console.log('Auto-restored session for:', user.email);
            }
        });
    }

    // =====================
    // EXPORT MODULE
    // =====================

    // Expose as global module
    window.Auth = {
        checkAndRestoreSession,
        ensureAuthenticated,
        hasPermission,
        requirePermission,
        logout,
        getCurrentUserSync,
        refreshUserData,
        clearSession
    };

    // Also expose individual functions for backward compatibility
    window.checkAndRestoreSession = checkAndRestoreSession;
    window.ensureAuthenticated = ensureAuthenticated;
    window.logout = logout;

    console.log('✅ Auth module initialized');
})();