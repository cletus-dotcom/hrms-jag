from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, date
from app import db
from app.models import User, Employee, Department, Position, Attendance, LeaveRequest, EmployeePDS

bp = Blueprint('routes', __name__)

@bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('routes.dashboard'))
    return redirect(url_for('routes.login'))

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('routes.dashboard'))
    
    if request.method == 'POST':
        employee_id = request.form.get('employee_id', '').strip()
        password = request.form.get('password', '')
        
        if not employee_id or not password:
            flash('Please enter both ID Number and Password.', 'error')
            return render_template('login.html')
        
        # Find user by employee_id
        user = User.query.filter_by(employee_id=employee_id).first()
        
        if user and user.check_password(password) and user.is_active:
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            # Store user info in session
            session['user_name'] = user.employee.full_name if user.employee else user.username
            session['user_role'] = user.role
            
            flash(f'Welcome back, {employee_id}!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('routes.dashboard'))
        else:
            flash('Invalid ID Number or Password.', 'error')
    
    return render_template('login.html')

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('routes.login'))

@bp.route('/dashboard')
@login_required
def dashboard():
    # Get statistics
    total_employees = Employee.query.filter_by(status='active').count()
    total_users = User.query.filter_by(is_active=True).count()
    pending_leaves = LeaveRequest.query.filter_by(status='pending').count()
    on_leave_today = LeaveRequest.query.filter(
        LeaveRequest.status == 'approved',
        LeaveRequest.start_date <= date.today(),
        LeaveRequest.end_date >= date.today()
    ).count()
    
    # Get recent leave requests
    recent_leaves = LeaveRequest.query.order_by(LeaveRequest.created_at.desc()).limit(10).all()
    
    # Get user's employee info
    employee = current_user.employee if current_user.employee else None
    
    return render_template('dashboard.html',
                         total_employees=total_employees,
                         total_users=total_users,
                         pending_leaves=pending_leaves,
                         on_leave_today=on_leave_today,
                         recent_leaves=recent_leaves,
                         employee=employee)

# Employee Management Routes
@bp.route('/employees')
@login_required
def employees_list():
    employees = Employee.query.order_by(Employee.created_at.desc()).all()
    departments = Department.query.all()
    employee = current_user.employee if current_user.employee else None
    return render_template('employees/list.html', employees=employees, departments=departments, employee=employee)

@bp.route('/employees/add', methods=['GET', 'POST'])
@login_required
def employee_add():
    if request.method == 'POST':
        try:
            employee_id = request.form.get('employee_id', '').strip()
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            middle_name = request.form.get('middle_name', '').strip()
            position = request.form.get('position', '').strip()
            department_id = request.form.get('department_id')
            status_of_appointment = request.form.get('status_of_appointment', '').strip()
            nature_of_appointment = request.form.get('nature_of_appointment', '').strip()
            appointment_date = request.form.get('appointment_date')
            phone = request.form.get('phone', '').strip()
            mobile_no = request.form.get('mobile_no', '').strip()
            # Address fields
            residential_house_no = request.form.get('residential_house_no', '').strip()
            residential_street = request.form.get('residential_street', '').strip()
            residential_subdivision = request.form.get('residential_subdivision', '').strip()
            residential_barangay = request.form.get('residential_barangay', '').strip()
            residential_city = request.form.get('residential_city', '').strip()
            residential_province = request.form.get('residential_province', '').strip()
            residential_zip_code = request.form.get('residential_zip_code', '').strip()
            address = request.form.get('address', '').strip()  # Keep for backward compatibility
            status = request.form.get('status', 'active')
            
            # Check if employee_id already exists
            if Employee.query.filter_by(employee_id=employee_id).first():
                flash('Employee ID already exists.', 'error')
                departments = Department.query.all()
                positions = Position.query.order_by(Position.title).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('employees/form.html', departments=departments, positions=positions, employee=employee)
            
            # Create employee
            new_employee = Employee(
                employee_id=employee_id,
                first_name=first_name,
                last_name=last_name,
                middle_name=middle_name if middle_name else None,
                position=position if position else None,
                department_id=int(department_id) if department_id else None,
                status_of_appointment=status_of_appointment if status_of_appointment else None,
                nature_of_appointment=nature_of_appointment if nature_of_appointment else None,
                appointment_date=datetime.strptime(appointment_date, '%Y-%m-%d').date() if appointment_date else None,
                phone=phone if phone else None,
                mobile_no=mobile_no if mobile_no else None,
                residential_house_no=residential_house_no if residential_house_no else None,
                residential_street=residential_street if residential_street else None,
                residential_subdivision=residential_subdivision if residential_subdivision else None,
                residential_barangay=residential_barangay if residential_barangay else None,
                residential_city=residential_city if residential_city else None,
                residential_province=residential_province if residential_province else None,
                residential_zip_code=residential_zip_code if residential_zip_code else None,
                address=address if address else None,  # Keep for backward compatibility
                status=status
            )
            
            db.session.add(new_employee)
            db.session.flush()  # Get the employee ID
            
            # Get user email if employee has a user account
            user_email = None
            if new_employee.user_id:
                user = User.query.get(new_employee.user_id)
                if user:
                    user_email = user.email
            
            # Create corresponding PDS record with address, mobile, phone, and email
            pds = EmployeePDS(
                employee_id=new_employee.id,
                surname=new_employee.last_name,
                first_name=new_employee.first_name,
                middle_name=new_employee.middle_name,
                telephone_no=phone if phone else None,
                mobile_no=mobile_no if mobile_no else None,
                email_address=user_email,
                residential_house_no=residential_house_no if residential_house_no else None,
                residential_street=residential_street if residential_street else None,
                residential_subdivision=residential_subdivision if residential_subdivision else None,
                residential_barangay=residential_barangay if residential_barangay else None,
                residential_city=residential_city if residential_city else None,
                residential_province=residential_province if residential_province else None,
                residential_zip_code=residential_zip_code if residential_zip_code else None
            )
            db.session.add(pds)
            db.session.commit()
            flash('Employee added successfully.', 'success')
            return redirect(url_for('routes.employees_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding employee: {str(e)}', 'error')
            departments = Department.query.all()
            positions = Position.query.order_by(Position.title).all()
            employee = current_user.employee if current_user.employee else None
            return render_template('employees/form.html', departments=departments, positions=positions, employee=employee)
    
    departments = Department.query.all()
    positions = Position.query.order_by(Position.title).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('employees/form.html', departments=departments, positions=positions, employee=employee)

@bp.route('/employees/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def employee_edit(id):
    emp = Employee.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            employee_id = request.form.get('employee_id', '').strip()
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            middle_name = request.form.get('middle_name', '').strip()
            position = request.form.get('position', '').strip()
            department_id = request.form.get('department_id')
            status_of_appointment = request.form.get('status_of_appointment', '').strip()
            nature_of_appointment = request.form.get('nature_of_appointment', '').strip()
            appointment_date = request.form.get('appointment_date')
            phone = request.form.get('phone', '').strip()
            mobile_no = request.form.get('mobile_no', '').strip()
            # Address fields
            residential_house_no = request.form.get('residential_house_no', '').strip()
            residential_street = request.form.get('residential_street', '').strip()
            residential_subdivision = request.form.get('residential_subdivision', '').strip()
            residential_barangay = request.form.get('residential_barangay', '').strip()
            residential_city = request.form.get('residential_city', '').strip()
            residential_province = request.form.get('residential_province', '').strip()
            residential_zip_code = request.form.get('residential_zip_code', '').strip()
            address = request.form.get('address', '').strip()  # Keep for backward compatibility
            status = request.form.get('status', 'active')
            
            # Check if employee_id already exists (excluding current employee)
            existing = Employee.query.filter_by(employee_id=employee_id).first()
            if existing and existing.id != id:
                flash('Employee ID already exists.', 'error')
                departments = Department.query.all()
                positions = Position.query.order_by(Position.title).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('employees/form.html', emp=emp, departments=departments, positions=positions, employee=employee)
            
            # Update employee
            emp.employee_id = employee_id
            emp.first_name = first_name
            emp.last_name = last_name
            emp.middle_name = middle_name if middle_name else None
            emp.position = position if position else None
            emp.department_id = int(department_id) if department_id else None
            emp.status_of_appointment = status_of_appointment if status_of_appointment else None
            emp.nature_of_appointment = nature_of_appointment if nature_of_appointment else None
            emp.appointment_date = datetime.strptime(appointment_date, '%Y-%m-%d').date() if appointment_date else None
            emp.phone = phone if phone else None
            emp.mobile_no = mobile_no if mobile_no else None
            emp.residential_house_no = residential_house_no if residential_house_no else None
            emp.residential_street = residential_street if residential_street else None
            emp.residential_subdivision = residential_subdivision if residential_subdivision else None
            emp.residential_barangay = residential_barangay if residential_barangay else None
            emp.residential_city = residential_city if residential_city else None
            emp.residential_province = residential_province if residential_province else None
            emp.residential_zip_code = residential_zip_code if residential_zip_code else None
            emp.address = address if address else None  # Keep for backward compatibility
            emp.status = status
            emp.updated_at = datetime.utcnow()
            
            # Get user email if employee has a user account
            user_email = None
            if emp.user_id:
                user = User.query.get(emp.user_id)
                if user:
                    user_email = user.email
            
            # Sync address, mobile, phone, and email to employee_pds table
            pds = EmployeePDS.query.filter_by(employee_id=id).first()
            if pds:
                pds.telephone_no = phone if phone else None
                pds.mobile_no = mobile_no if mobile_no else None
                pds.email_address = user_email
                pds.residential_house_no = residential_house_no if residential_house_no else None
                pds.residential_street = residential_street if residential_street else None
                pds.residential_subdivision = residential_subdivision if residential_subdivision else None
                pds.residential_barangay = residential_barangay if residential_barangay else None
                pds.residential_city = residential_city if residential_city else None
                pds.residential_province = residential_province if residential_province else None
                pds.residential_zip_code = residential_zip_code if residential_zip_code else None
            else:
                # Create PDS record if it doesn't exist
                pds = EmployeePDS(
                    employee_id=id,
                    surname=emp.last_name,
                    first_name=emp.first_name,
                    middle_name=emp.middle_name,
                    telephone_no=phone if phone else None,
                    mobile_no=mobile_no if mobile_no else None,
                    email_address=user_email,
                    residential_house_no=residential_house_no if residential_house_no else None,
                    residential_street=residential_street if residential_street else None,
                    residential_subdivision=residential_subdivision if residential_subdivision else None,
                    residential_barangay=residential_barangay if residential_barangay else None,
                    residential_city=residential_city if residential_city else None,
                    residential_province=residential_province if residential_province else None,
                    residential_zip_code=residential_zip_code if residential_zip_code else None
                )
                db.session.add(pds)
            
            db.session.commit()
            flash('Employee updated successfully.', 'success')
            return redirect(url_for('routes.employees_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating employee: {str(e)}', 'error')
            departments = Department.query.all()
            positions = Position.query.order_by(Position.title).all()
            employee = current_user.employee if current_user.employee else None
            return render_template('employees/form.html', emp=emp, departments=departments, positions=positions, employee=employee)
    
    departments = Department.query.all()
    positions = Position.query.order_by(Position.title).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('employees/form.html', emp=emp, departments=departments, positions=positions, employee=employee)

@bp.route('/employees/delete/<int:id>', methods=['POST'])
@login_required
def employee_delete(id):
    emp = Employee.query.get_or_404(id)
    
    try:
        # Check if employee has associated user
        if emp.user:
            flash('Cannot delete employee with associated user account. Delete the user account first.', 'error')
            return redirect(url_for('routes.employees_list'))
        
        db.session.delete(emp)
        db.session.commit()
        flash('Employee deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting employee: {str(e)}', 'error')
    
    return redirect(url_for('routes.employees_list'))

@bp.route('/employees/<int:id>/pds', methods=['GET', 'POST'])
@login_required
def employee_pds(id):
    emp = Employee.query.get_or_404(id)
    employee = current_user.employee if current_user.employee else None
    
    # Get or create PDS record
    pds = EmployeePDS.query.filter_by(employee_id=id).first()
    
    if request.method == 'POST':
        try:
            if not pds:
                pds = EmployeePDS(employee_id=id)
                db.session.add(pds)
            
            # Always sync name fields from Employee table (these should always be synced)
            pds.surname = emp.last_name
            pds.first_name = emp.first_name
            pds.middle_name = emp.middle_name
            
            # Check which section is being saved FIRST
            section = request.form.get('section', 'personal_information')
            
            # Only process Personal Information if this is the personal_information section
            if section == 'personal_information':
                # Personal Information
                pds.name_extension = request.form.get('name_extension', '').strip() or None
                dob = request.form.get('date_of_birth', '').strip()
                pds.date_of_birth = datetime.strptime(dob, '%Y-%m-%d').date() if dob else None
                pds.place_of_birth = request.form.get('place_of_birth', '').strip() or None
                pds.sex_at_birth = request.form.get('sex_at_birth', '').strip() or None
                pds.civil_status = request.form.get('civil_status', '').strip() or None
                pds.civil_status_other = request.form.get('civil_status_other', '').strip() or None
                pds.height = request.form.get('height', '').strip() or None
                pds.weight = request.form.get('weight', '').strip() or None
                pds.blood_type = request.form.get('blood_type', '').strip() or None
                pds.umid_id = request.form.get('umid_id', '').strip() or None
                pds.pagibig_id = request.form.get('pagibig_id', '').strip() or None
                pds.philhealth_no = request.form.get('philhealth_no', '').strip() or None
                pds.philsys_number = request.form.get('philsys_number', '').strip() or None
                pds.tin_no = request.form.get('tin_no', '').strip() or None
                # Item 15: Agency Employee No. is read-only and comes from employees.employee_id
                # It should not be saved from the form
                
                # Citizenship
                pds.citizenship = request.form.get('citizenship', '').strip() or None
                pds.dual_citizenship_type = request.form.get('dual_citizenship_type', '').strip() or None
                pds.dual_citizenship_country = request.form.get('dual_citizenship_country', '').strip() or None
                
                # Residential Address
                pds.residential_house_no = request.form.get('residential_house_no', '').strip() or None
                pds.residential_street = request.form.get('residential_street', '').strip() or None
                pds.residential_subdivision = request.form.get('residential_subdivision', '').strip() or None
                pds.residential_barangay = request.form.get('residential_barangay', '').strip() or None
                pds.residential_city = request.form.get('residential_city', '').strip() or None
                pds.residential_province = request.form.get('residential_province', '').strip() or None
                pds.residential_zip_code = request.form.get('residential_zip_code', '').strip() or None
                
                # Permanent Address
                same_as_residential = request.form.get('same_as_residential') == 'on'
                if same_as_residential:
                    # Copy residential address to permanent address
                    pds.permanent_house_no = emp.residential_house_no if emp.residential_house_no else pds.residential_house_no
                    pds.permanent_street = emp.residential_street if emp.residential_street else pds.residential_street
                    pds.permanent_subdivision = emp.residential_subdivision if emp.residential_subdivision else pds.residential_subdivision
                    pds.permanent_barangay = emp.residential_barangay if emp.residential_barangay else pds.residential_barangay
                    pds.permanent_city = emp.residential_city if emp.residential_city else pds.residential_city
                    pds.permanent_province = emp.residential_province if emp.residential_province else pds.residential_province
                    pds.permanent_zip_code = emp.residential_zip_code if emp.residential_zip_code else pds.residential_zip_code
                else:
                    # Use permanent address from form
                    pds.permanent_house_no = request.form.get('permanent_house_no', '').strip() or None
                    pds.permanent_street = request.form.get('permanent_street', '').strip() or None
                    pds.permanent_subdivision = request.form.get('permanent_subdivision', '').strip() or None
                    pds.permanent_barangay = request.form.get('permanent_barangay', '').strip() or None
                    pds.permanent_city = request.form.get('permanent_city', '').strip() or None
                    pds.permanent_province = request.form.get('permanent_province', '').strip() or None
                    pds.permanent_zip_code = request.form.get('permanent_zip_code', '').strip() or None
                
                # Contact Information
                # Telephone, mobile, email, and address are synced from Employee/User table, not from form
                pds.telephone_no = emp.phone if emp.phone else pds.telephone_no
                pds.mobile_no = emp.mobile_no if emp.mobile_no else pds.mobile_no
                # Get email from User table if employee has a user account
                if emp.user_id:
                    user = User.query.get(emp.user_id)
                    if user:
                        pds.email_address = user.email
                pds.residential_house_no = emp.residential_house_no if emp.residential_house_no else pds.residential_house_no
                pds.residential_street = emp.residential_street if emp.residential_street else pds.residential_street
                pds.residential_subdivision = emp.residential_subdivision if emp.residential_subdivision else pds.residential_subdivision
                pds.residential_barangay = emp.residential_barangay if emp.residential_barangay else pds.residential_barangay
                pds.residential_city = emp.residential_city if emp.residential_city else pds.residential_city
                pds.residential_province = emp.residential_province if emp.residential_province else pds.residential_province
                pds.residential_zip_code = emp.residential_zip_code if emp.residential_zip_code else pds.residential_zip_code
                
                db.session.commit()
                flash('Personal information saved successfully.', 'success')
            elif section == 'family_background':
                # Section II: Family Background
                # Section 22: Spouse's Information
                pds.spouse_surname = request.form.get('spouse_surname', '').strip() or None
                pds.spouse_first_name = request.form.get('spouse_first_name', '').strip() or None
                pds.spouse_middle_name = request.form.get('spouse_middle_name', '').strip() or None
                pds.spouse_name_extension = request.form.get('spouse_name_extension', '').strip() or None
                pds.spouse_occupation = request.form.get('spouse_occupation', '').strip() or None
                pds.spouse_employer = request.form.get('spouse_employer', '').strip() or None
                pds.spouse_business_address = request.form.get('spouse_business_address', '').strip() or None
                pds.spouse_telephone_no = request.form.get('spouse_telephone_no', '').strip() or None
                
                # Section 23: Children's Information (up to 20 children)
                for i in range(1, 21):
                    child_name = request.form.get(f'child{i}_name', '').strip() or None
                    child_dob = request.form.get(f'child{i}_date_of_birth', '').strip()
                    child_dob_date = datetime.strptime(child_dob, '%Y-%m-%d').date() if child_dob else None
                    setattr(pds, f'child{i}_name', child_name)
                    setattr(pds, f'child{i}_date_of_birth', child_dob_date)
                
                # Section 24: Father's Information
                pds.father_surname = request.form.get('father_surname', '').strip() or None
                pds.father_first_name = request.form.get('father_first_name', '').strip() or None
                pds.father_middle_name = request.form.get('father_middle_name', '').strip() or None
                pds.father_name_extension = request.form.get('father_name_extension', '').strip() or None
                
                # Section 25: Mother's Maiden Name
                pds.mother_surname = request.form.get('mother_surname', '').strip() or None
                pds.mother_first_name = request.form.get('mother_first_name', '').strip() or None
                pds.mother_middle_name = request.form.get('mother_middle_name', '').strip() or None
                
                db.session.commit()
                flash('Family background saved successfully.', 'success')
            elif section == 'educational_background':
                # Section III: Educational Background
                # Save all educational background entries (5 entries per level × 5 levels × 7 fields)
                levels = ['elementary', 'secondary', 'vocational', 'college', 'graduate']
                for level in levels:
                    for entry_num in range(1, 6):
                        school = request.form.get(f'{level}{entry_num}_school', '').strip() or None
                        degree = request.form.get(f'{level}{entry_num}_degree', '').strip() or None
                        from_date = request.form.get(f'{level}{entry_num}_from', '').strip() or None
                        to_date = request.form.get(f'{level}{entry_num}_to', '').strip() or None
                        highest_level = request.form.get(f'{level}{entry_num}_highest_level', '').strip() or None
                        year_graduated = request.form.get(f'{level}{entry_num}_year_graduated', '').strip() or None
                        honors = request.form.get(f'{level}{entry_num}_honors', '').strip() or None
                        
                        setattr(pds, f'{level}{entry_num}_school', school)
                        setattr(pds, f'{level}{entry_num}_degree', degree)
                        setattr(pds, f'{level}{entry_num}_from', from_date)
                        setattr(pds, f'{level}{entry_num}_to', to_date)
                        setattr(pds, f'{level}{entry_num}_highest_level', highest_level)
                        setattr(pds, f'{level}{entry_num}_year_graduated', year_graduated)
                        setattr(pds, f'{level}{entry_num}_honors', honors)
                
                db.session.commit()
                flash('Educational background saved successfully.', 'success')
            elif section == 'civil_service_eligibility':
                # Section IV: Civil Service Eligibility
                # Save all civil service eligibility entries (up to 10 entries)
                for entry_num in range(1, 11):
                    eligibility = request.form.get(f'civil_service{entry_num}_eligibility', '').strip() or None
                    rating = request.form.get(f'civil_service{entry_num}_rating', '').strip() or None
                    date = request.form.get(f'civil_service{entry_num}_date', '').strip() or None
                    place = request.form.get(f'civil_service{entry_num}_place', '').strip() or None
                    license_number = request.form.get(f'civil_service{entry_num}_license_number', '').strip() or None
                    license_valid_until = request.form.get(f'civil_service{entry_num}_license_valid_until', '').strip() or None
                    
                    setattr(pds, f'civil_service{entry_num}_eligibility', eligibility)
                    setattr(pds, f'civil_service{entry_num}_rating', rating)
                    setattr(pds, f'civil_service{entry_num}_date', date)
                    setattr(pds, f'civil_service{entry_num}_place', place)
                    setattr(pds, f'civil_service{entry_num}_license_number', license_number)
                    setattr(pds, f'civil_service{entry_num}_license_valid_until', license_valid_until)
                
                db.session.commit()
                flash('Civil service eligibility saved successfully.', 'success')
            elif section == 'work_experience':
                # Section V: Work Experience
                # Save all work experience entries (up to 20 entries)
                for entry_num in range(1, 21):
                    from_date = request.form.get(f'work_exp{entry_num}_from_date', '').strip() or None
                    to_date = request.form.get(f'work_exp{entry_num}_to_date', '').strip() or None
                    position_title = request.form.get(f'work_exp{entry_num}_position_title', '').strip() or None
                    department = request.form.get(f'work_exp{entry_num}_department', '').strip() or None
                    status_of_appointment = request.form.get(f'work_exp{entry_num}_status_of_appointment', '').strip() or None
                    govt_service = request.form.get(f'work_exp{entry_num}_govt_service', '').strip() or None
                    description_of_duties = request.form.get(f'work_exp{entry_num}_description_of_duties', '').strip() or None
                    
                    setattr(pds, f'work_exp{entry_num}_from_date', from_date)
                    setattr(pds, f'work_exp{entry_num}_to_date', to_date)
                    setattr(pds, f'work_exp{entry_num}_position_title', position_title)
                    setattr(pds, f'work_exp{entry_num}_department', department)
                    setattr(pds, f'work_exp{entry_num}_status_of_appointment', status_of_appointment)
                    setattr(pds, f'work_exp{entry_num}_govt_service', govt_service)
                    setattr(pds, f'work_exp{entry_num}_description_of_duties', description_of_duties)
                
                db.session.commit()
                flash('Work experience saved successfully.', 'success')
            elif section == 'voluntary_work':
                # Section VI: Voluntary Work
                # Save all voluntary work entries (up to 10 entries)
                for entry_num in range(1, 11):
                    organization = request.form.get(f'voluntary{entry_num}_organization', '').strip() or None
                    from_date = request.form.get(f'voluntary{entry_num}_from_date', '').strip() or None
                    to_date = request.form.get(f'voluntary{entry_num}_to_date', '').strip() or None
                    number_of_hours = request.form.get(f'voluntary{entry_num}_number_of_hours', '').strip() or None
                    position = request.form.get(f'voluntary{entry_num}_position', '').strip() or None
                    
                    setattr(pds, f'voluntary{entry_num}_organization', organization)
                    setattr(pds, f'voluntary{entry_num}_from_date', from_date)
                    setattr(pds, f'voluntary{entry_num}_to_date', to_date)
                    setattr(pds, f'voluntary{entry_num}_number_of_hours', number_of_hours)
                    setattr(pds, f'voluntary{entry_num}_position', position)
                
                db.session.commit()
                flash('Voluntary work saved successfully.', 'success')
            elif section == 'learning_development':
                # Section VII: Learning and Development (L&D) Interventions/Training Programs
                # Save all L&D entries (up to 50 entries)
                for entry_num in range(1, 51):
                    title = request.form.get(f'ld{entry_num}_title', '').strip() or None
                    from_date = request.form.get(f'ld{entry_num}_from_date', '').strip() or None
                    to_date = request.form.get(f'ld{entry_num}_to_date', '').strip() or None
                    number_of_hours = request.form.get(f'ld{entry_num}_number_of_hours', '').strip() or None
                    type_of_ld = request.form.get(f'ld{entry_num}_type_of_ld', '').strip() or None
                    conducted_by = request.form.get(f'ld{entry_num}_conducted_by', '').strip() or None
                    
                    setattr(pds, f'ld{entry_num}_title', title)
                    setattr(pds, f'ld{entry_num}_from_date', from_date)
                    setattr(pds, f'ld{entry_num}_to_date', to_date)
                    setattr(pds, f'ld{entry_num}_number_of_hours', number_of_hours)
                    setattr(pds, f'ld{entry_num}_type_of_ld', type_of_ld)
                    setattr(pds, f'ld{entry_num}_conducted_by', conducted_by)
                
                db.session.commit()
                flash('Learning and Development saved successfully.', 'success')
            elif section == 'other_information':
                # Section VIII: Other Information
                # Save Special Skills and Hobbies (Item 31) - up to 15 entries
                for entry_num in range(1, 16):
                    special_skill = request.form.get(f'special_skill{entry_num}', '').strip() or None
                    setattr(pds, f'special_skill{entry_num}', special_skill)
                
                # Save Non-Academic Distinctions/Recognition (Item 32) - up to 15 entries
                for entry_num in range(1, 16):
                    non_academic = request.form.get(f'non_academic{entry_num}', '').strip() or None
                    setattr(pds, f'non_academic{entry_num}', non_academic)
                
                # Save Membership in Association/Organization (Item 33) - up to 15 entries
                for entry_num in range(1, 16):
                    membership = request.form.get(f'membership{entry_num}', '').strip() or None
                    setattr(pds, f'membership{entry_num}', membership)
                
                # Save Item 34: Related by consanguinity or affinity
                related_third_degree = request.form.get('related_third_degree_yesno') == 'YES'
                related_fourth_degree = request.form.get('related_fourth_degree_yesno') == 'YES'
                related_details = request.form.get('related_details', '').strip() or None
                pds.related_third_degree = related_third_degree
                pds.related_fourth_degree = related_fourth_degree
                pds.related_details = related_details
                
                # Save Item 35: Administrative offense and criminal charges
                admin_offense_guilty = request.form.get('admin_offense_yesno') == 'YES'
                admin_offense_details = request.form.get('admin_offense_details', '').strip() or None
                criminally_charged = request.form.get('criminally_charged_yesno') == 'YES'
                criminal_charge_details = request.form.get('criminal_charge_details', '').strip() or None
                criminal_charge_date_filed = request.form.get('criminal_charge_date_filed', '').strip() or None
                criminal_charge_status = request.form.get('criminal_charge_status', '').strip() or None
                pds.admin_offense_guilty = admin_offense_guilty
                pds.admin_offense_details = admin_offense_details
                pds.criminally_charged = criminally_charged
                pds.criminal_charge_details = criminal_charge_details
                pds.criminal_charge_date_filed = criminal_charge_date_filed
                pds.criminal_charge_status = criminal_charge_status
                
                # Save Item 36: Convicted of crime
                convicted_crime = request.form.get('convicted_crime_yesno') == 'YES'
                convicted_crime_details = request.form.get('convicted_crime_details', '').strip() or None
                pds.convicted_crime = convicted_crime
                pds.convicted_crime_details = convicted_crime_details
                
                # Save Item 37: Separated from service
                separated_from_service = request.form.get('separated_from_service_yesno') == 'YES'
                separated_from_service_details = request.form.get('separated_from_service_details', '').strip() or None
                pds.separated_from_service = separated_from_service
                pds.separated_from_service_details = separated_from_service_details
                
                # Save Item 38: Political involvement
                candidate_in_election = request.form.get('candidate_in_election_yesno') == 'YES'
                candidate_in_election_details = request.form.get('candidate_in_election_details', '').strip() or None
                resigned_for_election = request.form.get('resigned_for_election_yesno') == 'YES'
                resigned_for_election_details = request.form.get('resigned_for_election_details', '').strip() or None
                pds.candidate_in_election = candidate_in_election
                pds.candidate_in_election_details = candidate_in_election_details
                pds.resigned_for_election = resigned_for_election
                pds.resigned_for_election_details = resigned_for_election_details
                
                # Save Item 39: Immigration status
                immigrant_permanent_resident = request.form.get('immigrant_permanent_resident_yesno') == 'YES'
                immigrant_country = request.form.get('immigrant_country', '').strip() or None
                pds.immigrant_permanent_resident = immigrant_permanent_resident
                pds.immigrant_country = immigrant_country
                
                # Save Item 40: Special groups
                indigenous_group_member = request.form.get('indigenous_group_member_yesno') == 'YES'
                indigenous_group_specify = request.form.get('indigenous_group_specify', '').strip() or None
                person_with_disability = request.form.get('person_with_disability_yesno') == 'YES'
                disability_id_no = request.form.get('disability_id_no', '').strip() or None
                solo_parent = request.form.get('solo_parent_yesno') == 'YES'
                solo_parent_id_no = request.form.get('solo_parent_id_no', '').strip() or None
                pds.indigenous_group_member = indigenous_group_member
                pds.indigenous_group_specify = indigenous_group_specify
                pds.person_with_disability = person_with_disability
                pds.disability_id_no = disability_id_no
                pds.solo_parent = solo_parent
                pds.solo_parent_id_no = solo_parent_id_no
                
                # Save Item 41: References (3 references)
                for ref_num in range(1, 4):
                    reference_name = request.form.get(f'reference{ref_num}_name', '').strip() or None
                    reference_address = request.form.get(f'reference{ref_num}_address', '').strip() or None
                    reference_contact = request.form.get(f'reference{ref_num}_contact', '').strip() or None
                    setattr(pds, f'reference{ref_num}_name', reference_name)
                    setattr(pds, f'reference{ref_num}_address', reference_address)
                    setattr(pds, f'reference{ref_num}_contact', reference_contact)
                
                # Item 42: Declaration (no fields to save, just text)
                
                # Government Issued ID (below Item 42)
                government_issued_id = request.form.get('government_issued_id', '').strip() or None
                id_license_passport_no = request.form.get('id_license_passport_no', '').strip() or None
                date_place_of_issuance = request.form.get('date_place_of_issuance', '').strip() or None
                pds.government_issued_id = government_issued_id
                pds.id_license_passport_no = id_license_passport_no
                pds.date_place_of_issuance = date_place_of_issuance
                
                db.session.commit()
                flash('Other information saved successfully.', 'success')
            else:
                # Unknown section - don't commit, just redirect with error
                flash('Unknown section. No data was saved.', 'error')
            
            return redirect(url_for('routes.employee_pds', id=id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving personal information: {str(e)}', 'error')
    
    # Get user email if employee has a user account
    user_email = None
    if emp.user_id:
        user = User.query.get(emp.user_id)
        if user:
            user_email = user.email
    
    # Ensure name fields are synced from Employee table for display
    # Also sync address, mobile, phone, and email from Employee/User table if PDS exists
    if pds:
        pds.surname = emp.last_name
        pds.first_name = emp.first_name
        pds.middle_name = emp.middle_name
        # Sync phone, address, mobile, and email from Employee/User table (source of truth)
        pds.telephone_no = emp.phone if emp.phone else pds.telephone_no
        pds.mobile_no = emp.mobile_no if emp.mobile_no else pds.mobile_no
        pds.email_address = user_email if user_email else pds.email_address
        pds.residential_house_no = emp.residential_house_no if emp.residential_house_no else pds.residential_house_no
        pds.residential_street = emp.residential_street if emp.residential_street else pds.residential_street
        pds.residential_subdivision = emp.residential_subdivision if emp.residential_subdivision else pds.residential_subdivision
        pds.residential_barangay = emp.residential_barangay if emp.residential_barangay else pds.residential_barangay
        pds.residential_city = emp.residential_city if emp.residential_city else pds.residential_city
        pds.residential_province = emp.residential_province if emp.residential_province else pds.residential_province
        pds.residential_zip_code = emp.residential_zip_code if emp.residential_zip_code else pds.residential_zip_code
        db.session.commit()
    else:
        # Create PDS record if it doesn't exist, syncing from Employee/User table
        pds = EmployeePDS(
            employee_id=id,
            surname=emp.last_name,
            first_name=emp.first_name,
            middle_name=emp.middle_name,
            telephone_no=emp.phone,
            mobile_no=emp.mobile_no,
            email_address=user_email,
            residential_house_no=emp.residential_house_no,
            residential_street=emp.residential_street,
            residential_subdivision=emp.residential_subdivision,
            residential_barangay=emp.residential_barangay,
            residential_city=emp.residential_city,
            residential_province=emp.residential_province,
            residential_zip_code=emp.residential_zip_code
        )
        db.session.add(pds)
        db.session.commit()
        db.session.refresh(pds)
    
    return render_template('employees/pds.html', emp=emp, pds=pds, employee=employee)

# User Management Routes
@bp.route('/users')
@login_required
def users_list():
    users = User.query.order_by(User.created_at.desc()).all()
    employees = Employee.query.filter_by(user_id=None).all()  # Employees without user accounts
    employee = current_user.employee if current_user.employee else None
    return render_template('users/list.html', users=users, employees=employees, employee=employee)

@bp.route('/users/add', methods=['GET', 'POST'])
@login_required
def user_add():
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            employee_id = request.form.get('employee_id', '').strip()
            role = request.form.get('role', 'employee')
            is_active = request.form.get('is_active') == 'on'
            
            # Check if username already exists
            if User.query.filter_by(username=username).first():
                flash('Username already exists.', 'error')
                employees = Employee.query.filter_by(user_id=None).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', employees=employees, employee=employee)
            
            # Check if email already exists
            if User.query.filter_by(email=email).first():
                flash('Email already exists.', 'error')
                employees = Employee.query.filter_by(user_id=None).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', employees=employees, employee=employee)
            
            # Check if employee_id already exists
            if User.query.filter_by(employee_id=employee_id).first():
                flash('Employee ID already has a user account.', 'error')
                employees = Employee.query.filter_by(user_id=None).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', employees=employees, employee=employee)
            
            # Create user
            new_user = User(
                username=username,
                email=email,
                employee_id=employee_id,
                role=role,
                is_active=is_active
            )
            new_user.set_password(password)
            
            db.session.add(new_user)
            db.session.flush()  # Get the user ID without committing
            
            # Link to employee if employee_id matches
            emp = Employee.query.filter_by(employee_id=employee_id).first()
            if emp:
                emp.user_id = new_user.id
            
            db.session.commit()
            flash('User added successfully.', 'success')
            return redirect(url_for('routes.users_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding user: {str(e)}', 'error')
            employees = Employee.query.filter_by(user_id=None).all()
            employee = current_user.employee if current_user.employee else None
            return render_template('users/form.html', employees=employees, employee=employee)
    
    employees = Employee.query.filter_by(user_id=None).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('users/form.html', employees=employees, employee=employee)

@bp.route('/users/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def user_edit(id):
    user = User.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '').strip()
            employee_id = request.form.get('employee_id', '').strip()
            role = request.form.get('role', 'employee')
            is_active = request.form.get('is_active') == 'on'
            
            # Check if username already exists (excluding current user)
            existing = User.query.filter_by(username=username).first()
            if existing and existing.id != id:
                flash('Username already exists.', 'error')
                employees = Employee.query.all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', user=user, employees=employees, employee=employee)
            
            # Check if email already exists (excluding current user)
            existing = User.query.filter_by(email=email).first()
            if existing and existing.id != id:
                flash('Email already exists.', 'error')
                employees = Employee.query.all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', user=user, employees=employees, employee=employee)
            
            # Check if employee_id already exists (excluding current user)
            existing = User.query.filter_by(employee_id=employee_id).first()
            if existing and existing.id != id:
                flash('Employee ID already has a user account.', 'error')
                employees = Employee.query.all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', user=user, employees=employees, employee=employee)
            
            # Update user
            user.username = username
            user.email = email
            user.employee_id = employee_id
            user.role = role
            user.is_active = is_active
            
            # Update password if provided
            if password:
                user.set_password(password)
            
            # Link to employee if employee_id matches
            emp = Employee.query.filter_by(employee_id=employee_id).first()
            if emp:
                emp.user_id = user.id
            
            db.session.commit()
            flash('User updated successfully.', 'success')
            return redirect(url_for('routes.users_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating user: {str(e)}', 'error')
            employees = Employee.query.all()
            employee = current_user.employee if current_user.employee else None
            return render_template('users/form.html', user=user, employees=employees, employee=employee)
    
    employees = Employee.query.all()
    employee = current_user.employee if current_user.employee else None
    return render_template('users/form.html', user=user, employees=employees, employee=employee)

@bp.route('/users/delete/<int:id>', methods=['POST'])
@login_required
def user_delete(id):
    user = User.query.get_or_404(id)
    
    # Prevent deleting yourself
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('routes.users_list'))
    
    try:
        # Unlink employee if exists
        if user.employee:
            user.employee.user_id = None
        
        db.session.delete(user)
        db.session.commit()
        flash('User deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting user: {str(e)}', 'error')
    
    return redirect(url_for('routes.users_list'))

# Department Management Routes
@bp.route('/departments')
@login_required
def departments_list():
    departments = Department.query.order_by(Department.created_at.desc()).all()
    employees = Employee.query.all()
    employee = current_user.employee if current_user.employee else None
    return render_template('departments/list.html', departments=departments, employees=employees, employee=employee)

@bp.route('/departments/add', methods=['GET', 'POST'])
@login_required
def department_add():
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            manager_id = request.form.get('manager_id')
            
            # Check if department name already exists
            if Department.query.filter_by(name=name).first():
                flash('Department name already exists.', 'error')
                employees = Employee.query.all()
                employee = current_user.employee if current_user.employee else None
                return render_template('departments/form.html', employees=employees, employee=employee)
            
            # Create department
            new_department = Department(
                name=name,
                description=description if description else None,
                manager_id=int(manager_id) if manager_id else None
            )
            
            db.session.add(new_department)
            db.session.commit()
            flash('Department added successfully.', 'success')
            return redirect(url_for('routes.departments_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding department: {str(e)}', 'error')
            employees = Employee.query.all()
            employee = current_user.employee if current_user.employee else None
            return render_template('departments/form.html', employees=employees, employee=employee)
    
    employees = Employee.query.all()
    employee = current_user.employee if current_user.employee else None
    return render_template('departments/form.html', employees=employees, employee=employee)

@bp.route('/departments/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def department_edit(id):
    dept = Department.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            manager_id = request.form.get('manager_id')
            
            # Check if department name already exists (excluding current department)
            existing = Department.query.filter_by(name=name).first()
            if existing and existing.id != id:
                flash('Department name already exists.', 'error')
                employees = Employee.query.all()
                employee = current_user.employee if current_user.employee else None
                return render_template('departments/form.html', dept=dept, employees=employees, employee=employee)
            
            # Update department
            dept.name = name
            dept.description = description if description else None
            dept.manager_id = int(manager_id) if manager_id else None
            
            db.session.commit()
            flash('Department updated successfully.', 'success')
            return redirect(url_for('routes.departments_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating department: {str(e)}', 'error')
            employees = Employee.query.all()
            employee = current_user.employee if current_user.employee else None
            return render_template('departments/form.html', dept=dept, employees=employees, employee=employee)
    
    employees = Employee.query.all()
    employee = current_user.employee if current_user.employee else None
    return render_template('departments/form.html', dept=dept, employees=employees, employee=employee)

@bp.route('/departments/delete/<int:id>', methods=['POST'])
@login_required
def department_delete(id):
    dept = Department.query.get_or_404(id)
    
    try:
        # Check if department has employees
        if dept.employees:
            flash('Cannot delete department with assigned employees. Please reassign employees first.', 'error')
            return redirect(url_for('routes.departments_list'))
        
        db.session.delete(dept)
        db.session.commit()
        flash('Department deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting department: {str(e)}', 'error')
    
    return redirect(url_for('routes.departments_list'))


# Position Management Routes
@bp.route('/positions')
@login_required
def positions_list():
    positions = Position.query.order_by(Position.title).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('positions/list.html', positions=positions, employee=employee)


@bp.route('/positions/add', methods=['GET', 'POST'])
@login_required
def position_add():
    if request.method == 'POST':
        try:
            title = request.form.get('title', '').strip()
            if not title:
                flash('Title is required.', 'error')
                employee = current_user.employee if current_user.employee else None
                return render_template('positions/form.html', employee=employee)
            if Position.query.filter_by(title=title).first():
                flash('Position title already exists.', 'error')
                employee = current_user.employee if current_user.employee else None
                return render_template('positions/form.html', employee=employee)
            new_position = Position(title=title)
            db.session.add(new_position)
            db.session.commit()
            flash('Position added successfully.', 'success')
            return redirect(url_for('routes.positions_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding position: {str(e)}', 'error')
            employee = current_user.employee if current_user.employee else None
            return render_template('positions/form.html', employee=employee)
    employee = current_user.employee if current_user.employee else None
    return render_template('positions/form.html', employee=employee)


@bp.route('/positions/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def position_edit(id):
    pos = Position.query.get_or_404(id)
    if request.method == 'POST':
        try:
            title = request.form.get('title', '').strip()
            if not title:
                flash('Title is required.', 'error')
                employee = current_user.employee if current_user.employee else None
                return render_template('positions/form.html', position=pos, employee=employee)
            existing = Position.query.filter_by(title=title).first()
            if existing and existing.id != id:
                flash('Position title already exists.', 'error')
                employee = current_user.employee if current_user.employee else None
                return render_template('positions/form.html', position=pos, employee=employee)
            pos.title = title
            db.session.commit()
            flash('Position updated successfully.', 'success')
            return redirect(url_for('routes.positions_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating position: {str(e)}', 'error')
            employee = current_user.employee if current_user.employee else None
            return render_template('positions/form.html', position=pos, employee=employee)
    employee = current_user.employee if current_user.employee else None
    return render_template('positions/form.html', position=pos, employee=employee)


@bp.route('/positions/delete/<int:id>', methods=['POST'])
@login_required
def position_delete(id):
    pos = Position.query.get_or_404(id)
    try:
        db.session.delete(pos)
        db.session.commit()
        flash('Position deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting position: {str(e)}', 'error')
    return redirect(url_for('routes.positions_list'))
