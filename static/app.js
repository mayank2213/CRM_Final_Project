document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.modal').forEach((modal) => {
    modal.addEventListener('show.bs.modal', (event) => {
      const button = event.relatedTarget;
      const form = modal.querySelector('form');
      modal.querySelector('.modal-title').textContent = button.dataset.modalTitle || 'Edit';
      form.querySelectorAll('input[name], textarea[name], select[name]').forEach((field) => {
        const key = field.name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        if (field.type !== 'submit') field.value = button.dataset[key] || '';
      });
    });
  });
});
