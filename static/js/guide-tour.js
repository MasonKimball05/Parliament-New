/**
 * Parliament Guide Tour Engine
 *
 * Provides interactive step-by-step tours of features.
 * Tours highlight elements on the page and show tooltips with instructions.
 */

class GuideTour {
    constructor(options = {}) {
        this.tourData = null;
        this.currentStep = 0;
        this.isActive = false;
        this.overlay = null;
        this.tooltip = null;
        this.highlightBox = null;
        this.onComplete = options.onComplete || (() => {});
        this.onSkip = options.onSkip || (() => {});
        this.csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
    }

    /**
     * Start a tour by its slug
     */
    async start(tourSlug, restart = false) {
        try {
            const url = `/guide/tour/${tourSlug}/start/${restart ? '?restart=true' : ''}`;
            const response = await fetch(url);

            if (!response.ok) {
                console.error('Failed to load tour:', response.statusText);
                return false;
            }

            this.tourData = await response.json();
            this.currentStep = this.tourData.current_step || 0;

            if (this.tourData.completed && !restart) {
                // Tour already completed, ask if they want to restart
                if (confirm('You have already completed this tour. Would you like to restart it?')) {
                    return this.start(tourSlug, true);
                }
                return false;
            }

            this.isActive = true;
            this.createOverlay();
            this.showStep(this.currentStep);
            return true;
        } catch (error) {
            console.error('Error starting tour:', error);
            return false;
        }
    }

    /**
     * Create the overlay and tooltip elements
     */
    createOverlay() {
        // Remove any existing tour elements
        this.cleanup();

        // Create overlay
        this.overlay = document.createElement('div');
        this.overlay.className = 'guide-tour-overlay';
        this.overlay.innerHTML = `
            <style>
                .guide-tour-overlay {
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    background: rgba(0, 0, 0, 0.5);
                    z-index: 9998;
                    pointer-events: none;
                }
                .guide-tour-highlight {
                    position: absolute;
                    background: transparent;
                    box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.5);
                    border-radius: 4px;
                    z-index: 9999;
                    pointer-events: auto;
                    transition: all 0.3s ease;
                }
                .guide-tour-tooltip {
                    position: absolute;
                    background: white;
                    border-radius: 8px;
                    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
                    padding: 20px;
                    max-width: 400px;
                    min-width: 300px;
                    z-index: 10000;
                    pointer-events: auto;
                }
                .dark .guide-tour-tooltip {
                    background: #1f2937;
                    color: #f3f4f6;
                }
                .guide-tour-tooltip-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 12px;
                }
                .guide-tour-tooltip-title {
                    font-size: 1.125rem;
                    font-weight: 600;
                    color: #111827;
                }
                .dark .guide-tour-tooltip-title {
                    color: #f3f4f6;
                }
                .guide-tour-tooltip-step {
                    font-size: 0.75rem;
                    color: #6b7280;
                    background: #f3f4f6;
                    padding: 2px 8px;
                    border-radius: 4px;
                }
                .dark .guide-tour-tooltip-step {
                    background: #374151;
                    color: #9ca3af;
                }
                .guide-tour-tooltip-content {
                    color: #4b5563;
                    line-height: 1.6;
                    margin-bottom: 16px;
                }
                .dark .guide-tour-tooltip-content {
                    color: #d1d5db;
                }
                .guide-tour-tooltip-actions {
                    display: flex;
                    justify-content: space-between;
                    gap: 8px;
                }
                .guide-tour-btn {
                    padding: 8px 16px;
                    border-radius: 6px;
                    font-size: 0.875rem;
                    font-weight: 500;
                    cursor: pointer;
                    transition: all 0.2s;
                    border: none;
                }
                .guide-tour-btn-primary {
                    background: #2563eb;
                    color: white;
                }
                .guide-tour-btn-primary:hover {
                    background: #1d4ed8;
                }
                .guide-tour-btn-secondary {
                    background: #f3f4f6;
                    color: #374151;
                }
                .dark .guide-tour-btn-secondary {
                    background: #374151;
                    color: #d1d5db;
                }
                .guide-tour-btn-secondary:hover {
                    background: #e5e7eb;
                }
                .dark .guide-tour-btn-secondary:hover {
                    background: #4b5563;
                }
                .guide-tour-btn-skip {
                    background: transparent;
                    color: #6b7280;
                    padding: 8px;
                }
                .guide-tour-btn-skip:hover {
                    color: #374151;
                }
                .dark .guide-tour-btn-skip:hover {
                    color: #d1d5db;
                }
                .guide-tour-arrow {
                    position: absolute;
                    width: 12px;
                    height: 12px;
                    background: white;
                    transform: rotate(45deg);
                }
                .dark .guide-tour-arrow {
                    background: #1f2937;
                }
                .guide-tour-arrow-top {
                    bottom: -6px;
                    left: 50%;
                    margin-left: -6px;
                }
                .guide-tour-arrow-bottom {
                    top: -6px;
                    left: 50%;
                    margin-left: -6px;
                }
                .guide-tour-arrow-left {
                    right: -6px;
                    top: 50%;
                    margin-top: -6px;
                }
                .guide-tour-arrow-right {
                    left: -6px;
                    top: 50%;
                    margin-top: -6px;
                }
            </style>
        `;
        document.body.appendChild(this.overlay);

        // Create highlight box
        this.highlightBox = document.createElement('div');
        this.highlightBox.className = 'guide-tour-highlight';
        document.body.appendChild(this.highlightBox);

        // Create tooltip
        this.tooltip = document.createElement('div');
        this.tooltip.className = 'guide-tour-tooltip';
        document.body.appendChild(this.tooltip);
    }

    /**
     * Show a specific step
     */
    showStep(stepIndex) {
        if (!this.tourData || !this.tourData.steps || stepIndex >= this.tourData.steps.length) {
            this.complete();
            return;
        }

        const step = this.tourData.steps[stepIndex];
        this.currentStep = stepIndex;

        // Check if we need to navigate to a different page
        if (step.target_page && !window.location.pathname.startsWith(step.target_page)) {
            // Store current state and redirect
            sessionStorage.setItem('guideTour', JSON.stringify({
                slug: this.tourData.tour.slug,
                step: stepIndex
            }));
            window.location.href = step.target_page;
            return;
        }

        // Find target element
        let targetEl = null;
        if (step.target_selector) {
            targetEl = document.querySelector(step.target_selector);
        }

        // Position highlight and tooltip
        this.positionElements(targetEl, step);

        // Render tooltip content
        this.renderTooltip(step, stepIndex);

        // Handle wait_for_click
        if (step.wait_for_click && targetEl) {
            const clickHandler = () => {
                targetEl.removeEventListener('click', clickHandler);
                this.next();
            };
            targetEl.addEventListener('click', clickHandler);
        }
    }

    /**
     * Position the highlight box and tooltip
     */
    positionElements(targetEl, step) {
        if (targetEl) {
            const rect = targetEl.getBoundingClientRect();
            const padding = 8;

            // Position highlight box
            this.highlightBox.style.top = `${rect.top + window.scrollY - padding}px`;
            this.highlightBox.style.left = `${rect.left + window.scrollX - padding}px`;
            this.highlightBox.style.width = `${rect.width + padding * 2}px`;
            this.highlightBox.style.height = `${rect.height + padding * 2}px`;
            this.highlightBox.style.display = 'block';

            // Scroll element into view
            targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });

            // Position tooltip based on position preference
            setTimeout(() => this.positionTooltip(rect, step.position), 100);
        } else {
            // No target element - center the tooltip
            this.highlightBox.style.display = 'none';
            this.centerTooltip();
        }
    }

    /**
     * Position tooltip relative to target element
     */
    positionTooltip(targetRect, position) {
        const tooltipRect = this.tooltip.getBoundingClientRect();
        const margin = 16;
        let top, left;
        let arrowClass = '';

        switch (position) {
            case 'top':
                top = targetRect.top + window.scrollY - tooltipRect.height - margin;
                left = targetRect.left + window.scrollX + (targetRect.width - tooltipRect.width) / 2;
                arrowClass = 'guide-tour-arrow-top';
                break;
            case 'bottom':
                top = targetRect.bottom + window.scrollY + margin;
                left = targetRect.left + window.scrollX + (targetRect.width - tooltipRect.width) / 2;
                arrowClass = 'guide-tour-arrow-bottom';
                break;
            case 'left':
                top = targetRect.top + window.scrollY + (targetRect.height - tooltipRect.height) / 2;
                left = targetRect.left + window.scrollX - tooltipRect.width - margin;
                arrowClass = 'guide-tour-arrow-left';
                break;
            case 'right':
                top = targetRect.top + window.scrollY + (targetRect.height - tooltipRect.height) / 2;
                left = targetRect.right + window.scrollX + margin;
                arrowClass = 'guide-tour-arrow-right';
                break;
            default: // center
                this.centerTooltip();
                return;
        }

        // Ensure tooltip stays within viewport
        left = Math.max(margin, Math.min(left, window.innerWidth - tooltipRect.width - margin));
        top = Math.max(margin + window.scrollY, top);

        this.tooltip.style.top = `${top}px`;
        this.tooltip.style.left = `${left}px`;

        // Add arrow
        const existingArrow = this.tooltip.querySelector('.guide-tour-arrow');
        if (existingArrow) existingArrow.remove();

        if (arrowClass) {
            const arrow = document.createElement('div');
            arrow.className = `guide-tour-arrow ${arrowClass}`;
            this.tooltip.appendChild(arrow);
        }
    }

    /**
     * Center tooltip on screen (for modal steps)
     */
    centerTooltip() {
        const tooltipRect = this.tooltip.getBoundingClientRect();
        this.tooltip.style.top = `${window.scrollY + (window.innerHeight - tooltipRect.height) / 2}px`;
        this.tooltip.style.left = `${(window.innerWidth - tooltipRect.width) / 2}px`;
    }

    /**
     * Render tooltip content
     */
    renderTooltip(step, stepIndex) {
        const totalSteps = this.tourData.steps.length;
        const isFirst = stepIndex === 0;
        const isLast = stepIndex === totalSteps - 1;

        this.tooltip.innerHTML = `
            <div class="guide-tour-tooltip-header">
                <span class="guide-tour-tooltip-title">${this.escapeHtml(step.title)}</span>
                <span class="guide-tour-tooltip-step">${stepIndex + 1} / ${totalSteps}</span>
            </div>
            <div class="guide-tour-tooltip-content">
                ${this.escapeHtml(step.content)}
            </div>
            <div class="guide-tour-tooltip-actions">
                <div>
                    <button class="guide-tour-btn guide-tour-btn-skip" id="tour-skip">
                        Skip tour
                    </button>
                </div>
                <div style="display: flex; gap: 8px;">
                    ${!isFirst ? '<button class="guide-tour-btn guide-tour-btn-secondary" id="tour-prev">Previous</button>' : ''}
                    <button class="guide-tour-btn guide-tour-btn-primary" id="tour-next">
                        ${isLast ? 'Finish' : 'Next'}
                    </button>
                </div>
            </div>
        `;

        // Attach event listeners
        this.tooltip.querySelector('#tour-skip')?.addEventListener('click', () => this.skip());
        this.tooltip.querySelector('#tour-prev')?.addEventListener('click', () => this.previous());
        this.tooltip.querySelector('#tour-next')?.addEventListener('click', () => this.next());
    }

    /**
     * Go to next step
     */
    async next() {
        // Advance on server
        try {
            await fetch(`/guide/tour/${this.tourData.tour.slug}/advance/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.csrfToken
                }
            });
        } catch (error) {
            console.error('Error advancing tour:', error);
        }

        if (this.currentStep < this.tourData.steps.length - 1) {
            this.showStep(this.currentStep + 1);
        } else {
            this.complete();
        }
    }

    /**
     * Go to previous step
     */
    previous() {
        if (this.currentStep > 0) {
            this.showStep(this.currentStep - 1);
        }
    }

    /**
     * Complete the tour
     */
    async complete() {
        try {
            await fetch(`/guide/tour/${this.tourData.tour.slug}/complete/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.csrfToken
                }
            });
        } catch (error) {
            console.error('Error completing tour:', error);
        }

        this.cleanup();
        this.isActive = false;
        this.onComplete(this.tourData.tour);

        // Show completion message
        this.showCompletionMessage();
    }

    /**
     * Skip the tour
     */
    async skip() {
        try {
            await fetch(`/guide/tour/${this.tourData.tour.slug}/skip/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.csrfToken
                }
            });
        } catch (error) {
            console.error('Error skipping tour:', error);
        }

        this.cleanup();
        this.isActive = false;
        this.onSkip(this.tourData.tour);
    }

    /**
     * Show completion message
     */
    showCompletionMessage() {
        const toast = document.createElement('div');
        toast.className = 'fixed bottom-4 right-4 bg-green-500 text-white px-6 py-3 rounded-lg shadow-lg z-50 flex items-center';
        toast.innerHTML = `
            <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
            </svg>
            Tour completed!
        `;
        document.body.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.3s';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    /**
     * Clean up tour elements
     */
    cleanup() {
        this.overlay?.remove();
        this.tooltip?.remove();
        this.highlightBox?.remove();
        this.overlay = null;
        this.tooltip = null;
        this.highlightBox = null;
        sessionStorage.removeItem('guideTour');
    }

    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Check for resumed tour on page load
document.addEventListener('DOMContentLoaded', () => {
    const savedTour = sessionStorage.getItem('guideTour');
    if (savedTour) {
        const { slug, step } = JSON.parse(savedTour);
        const tour = new GuideTour();
        tour.start(slug).then(() => {
            tour.showStep(step);
        });
    }
});

// Export for use
window.GuideTour = GuideTour;
